from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageDraw, ImageTk
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdf_canvas

try:
    import pypdfium2 as pdfium
except ImportError:
    pdfium = None


APP_NAME = "PDF Local Studio"
BG = "#101827"
PANEL = "#182235"
PANEL_2 = "#202c42"
TEXT = "#f3f6fb"
MUTED = "#9dacbf"
ACCENT = "#f59e0b"
ACCENT_DARK = "#c77c04"
SUCCESS = "#39c98a"


def app_data_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    folder = root / "PDFLocalStudio"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "signatures").mkdir(exist_ok=True)
    return folder


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def find_pdftoppm() -> str | None:
    bundled = resource_path("poppler") / "pdftoppm.exe"
    if bundled.exists():
        return str(bundled)
    found = shutil.which("pdftoppm")
    if found:
        return found
    known = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe"
    return str(known) if known.exists() else None


@dataclass
class Placement:
    page: int
    signature: str
    x: float
    y: float
    width: float
    height: float


def parse_split_block(value: str | None, total_pages: int) -> int:
    """Converte '1-2' ou '2' na quantidade de páginas por arquivo."""
    token = (value or "1").strip().replace(" ", "")
    try:
        if "-" in token:
            parts = token.split("-")
            if len(parts) != 2:
                raise ValueError
            start, end = map(int, parts)
            if start != 1 or end < start:
                raise ValueError
            size = end - start + 1
        else:
            size = int(token)
    except (TypeError, ValueError):
        raise ValueError("Use um número ou um intervalo iniciado em 1, como 1-2.")
    if size < 1 or size > total_pages:
        raise ValueError(f"O bloco deve conter entre 1 e {total_pages} página(s).")
    return size


def split_page_groups(total_pages: int, block_size: int) -> list[tuple[int, int]]:
    """Retorna blocos sequenciais usando páginas 1-based e fim inclusivo."""
    return [(start + 1, min(start + block_size, total_pages))
            for start in range(0, total_pages, block_size)]


class DrawSignatureDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, output: Path):
        super().__init__(parent)
        self.output = output
        self.saved = False
        self.last = None
        self.title("Desenhar assinatura")
        self.geometry("720x360")
        self.configure(bg=BG)
        self.transient(parent)
        self.grab_set()
        tk.Label(self, text="Desenhe com o mouse", bg=BG, fg=TEXT,
                 font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=20, pady=(16, 8))
        self.pad = tk.Canvas(self, bg="white", width=680, height=230, cursor="pencil",
                             highlightthickness=0)
        self.pad.pack(padx=20)
        self.pad.bind("<Button-1>", self.start)
        self.pad.bind("<B1-Motion>", self.paint)
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=20, pady=14)
        self.button(row, "Limpar", self.clear, PANEL_2).pack(side="left")
        self.button(row, "Salvar assinatura", self.save, ACCENT, "#111827").pack(side="right")

    @staticmethod
    def button(parent, text, command, bg, fg=TEXT):
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                         activebackground=ACCENT_DARK, activeforeground="white",
                         relief="flat", padx=18, pady=8, font=("Segoe UI", 10, "bold"), cursor="hand2")

    def start(self, event):
        self.last = (event.x, event.y)

    def paint(self, event):
        if self.last:
            self.pad.create_line(*self.last, event.x, event.y, fill="#111827", width=4,
                                 capstyle=tk.ROUND, smooth=True)
        self.last = (event.x, event.y)

    def clear(self):
        self.pad.delete("all")

    def save(self):
        lines = self.pad.find_all()
        if not lines:
            messagebox.showwarning(APP_NAME, "Desenhe uma assinatura antes de salvar.", parent=self)
            return
        image = Image.new("RGBA", (680, 230), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)
        for item in lines:
            coords = self.pad.coords(item)
            for i in range(0, len(coords) - 2, 2):
                draw.line((coords[i], coords[i + 1], coords[i + 2], coords[i + 3]),
                          fill=(17, 24, 39, 255), width=5)
        bbox = image.getbbox()
        if bbox:
            image = image.crop((max(0, bbox[0] - 8), max(0, bbox[1] - 8),
                                min(image.width, bbox[2] + 8), min(image.height, bbox[3] + 8)))
        image.save(self.output, "PNG")
        self.saved = True
        self.destroy()


class FoxPDFLocal(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1240x790")
        self.minsize(980, 650)
        self.configure(bg=BG)
        self.pdf_path: Path | None = None
        self.reader: PdfReader | None = None
        self.current_page = 0
        self.page_photo = None
        self.page_image_size = (1, 1)
        self.page_canvas_box = (0, 0, 1, 1)
        self.placements: list[Placement] = []
        self.active_signature: Path | None = None
        self.active_overlay = None
        self.overlay_items: dict[int, dict] = {}
        self.drag_offset = (0, 0)
        self.selection_box = None
        self.resize_handle = None
        self.resizing = False
        self.resize_origin = (0, 0)
        self.resize_source = None
        self.signature_scale = tk.DoubleVar(value=28)
        self.status_var = tk.StringVar(value="Pronto. Abra um PDF para começar.")
        self.page_var = tk.StringVar(value="Nenhum documento")
        self._build_style()
        self._build_ui()
        self.refresh_signatures()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Dark.Horizontal.TScale", background=PANEL, troughcolor="#36445a")
        style.configure("Dark.TCombobox", fieldbackground=PANEL_2, background=PANEL_2,
                        foreground=TEXT, arrowcolor=TEXT)

    def button(self, parent, text, command, primary=False, width=None):
        bg = ACCENT if primary else PANEL_2
        fg = "#111827" if primary else TEXT
        return tk.Button(parent, text=text, command=command, bg=bg, fg=fg,
                         activebackground=ACCENT_DARK if primary else "#2e3d57",
                         activeforeground="white", relief="flat", bd=0, padx=14, pady=9,
                         width=width, font=("Segoe UI", 9, "bold"), cursor="hand2")

    def _build_ui(self):
        top = tk.Frame(self, bg=PANEL, height=66)
        top.pack(fill="x")
        top.pack_propagate(False)
        brand = tk.Frame(top, bg=PANEL)
        brand.pack(side="left", padx=20)
        tk.Label(brand, text="PDF", bg=PANEL, fg=ACCENT, font=("Segoe UI", 18, "bold")).pack(side="left")
        tk.Label(brand, text=" LOCAL STUDIO", bg=PANEL, fg=TEXT, font=("Segoe UI", 18, "bold")).pack(side="left")
        tk.Label(brand, text="  by JDM", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 8, "italic")).pack(side="left", anchor="s", pady=(0, 5))
        self.button(top, "Abrir PDF", self.open_pdf, True).pack(side="left", padx=18)
        self.button(top, "Salvar PDF assinado", self.save_signed_pdf).pack(side="left", padx=4)
        tk.Label(top, text="100% local • seus arquivos não saem do PC", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="right", padx=22)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)
        side = tk.Frame(body, bg=PANEL, width=280)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="FERRAMENTAS", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(18, 8))
        for label, command in (("✒  Assinar documento", self.add_signature),
                               ("⇄  Juntar PDFs", self.merge_pdfs),
                               ("✂  Dividir PDF", self.split_pdf),
                               ("↓  Comprimir PDF", self.compress_pdf)):
            self.button(side, label, command, width=27).pack(padx=16, pady=4, fill="x")

        tk.Frame(side, bg="#334158", height=1).pack(fill="x", padx=16, pady=16)
        tk.Label(side, text="MINHAS ASSINATURAS", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18)
        sig_actions = tk.Frame(side, bg=PANEL)
        sig_actions.pack(fill="x", padx=16, pady=8)
        self.button(sig_actions, "+ Importar", self.import_signature).pack(side="left", expand=True, fill="x", padx=(0, 3))
        self.button(sig_actions, "✎ Desenhar", self.draw_signature).pack(side="left", expand=True, fill="x", padx=(3, 0))
        self.signature_list = tk.Listbox(side, bg=PANEL_2, fg=TEXT, selectbackground=ACCENT_DARK,
                                         selectforeground="white", borderwidth=0, highlightthickness=0,
                                         font=("Segoe UI", 10), height=8)
        self.signature_list.pack(fill="x", padx=16)
        self.signature_list.bind("<<ListboxSelect>>", self.select_signature)
        self.button(side, "Excluir selecionada", self.delete_signature).pack(padx=16, pady=7, fill="x")
        tk.Label(side, text="Tamanho da assinatura", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=18, pady=(10, 0))
        ttk.Scale(side, from_=12, to=65, variable=self.signature_scale,
                  style="Dark.Horizontal.TScale", command=lambda _v: self.resize_overlay()).pack(fill="x", padx=18, pady=8)

        center = tk.Frame(body, bg=BG)
        center.pack(side="left", fill="both", expand=True)
        nav = tk.Frame(center, bg=BG, height=48)
        nav.pack(fill="x")
        self.button(nav, "‹", self.previous_page).pack(side="left", padx=(18, 4), pady=7)
        self.button(nav, "›", self.next_page).pack(side="left", pady=7)
        tk.Label(nav, textvariable=self.page_var, bg=BG, fg=TEXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left", padx=15)
        self.canvas = tk.Canvas(center, bg="#0b101b", highlightthickness=0, cursor="arrow")
        self.canvas.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.canvas.bind("<Configure>", lambda _e: self.render_page())
        self.canvas.bind("<Button-1>", self.canvas_click)
        self.canvas.bind("<B1-Motion>", self.canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.finish_resize)
        self.canvas.bind("<Delete>", lambda _e: self.remove_overlay())

        status = tk.Label(self, textvariable=self.status_var, anchor="w", bg="#0b101b", fg=MUTED,
                          font=("Segoe UI", 9), padx=14, pady=7)
        status.pack(fill="x")

    def set_status(self, text: str):
        self.status_var.set(text)
        self.update_idletasks()

    def run_task(self, label, fn):
        self.set_status(label)
        def worker():
            try:
                message = fn()
                self.after(0, lambda: self.set_status(message or "Concluído."))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror(APP_NAME, str(exc), parent=self))
                self.after(0, lambda: self.set_status("A operação não pôde ser concluída."))
        threading.Thread(target=worker, daemon=True).start()

    def open_pdf(self):
        name = filedialog.askopenfilename(title="Abrir PDF", filetypes=[("Arquivo PDF", "*.pdf")])
        if not name:
            return
        try:
            self.pdf_path = Path(name)
            self.reader = PdfReader(name)
            self.current_page = 0
            self.placements.clear()
            self.active_overlay = None
            self.overlay_items.clear()
            self.selection_box = None
            self.resize_handle = None
            self.title(f"{APP_NAME} — {self.pdf_path.name}")
            self.render_page()
            self.set_status(f"Documento aberto: {self.pdf_path.name}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Não foi possível abrir o PDF.\n\n{exc}")

    def render_page(self):
        if not self.reader or not self.pdf_path or self.canvas.winfo_width() < 50:
            self.canvas.delete("all")
            self.canvas.create_text(max(100, self.canvas.winfo_width() // 2),
                                    max(100, self.canvas.winfo_height() // 2),
                                    text="Abra um PDF para visualizar e assinar", fill=MUTED,
                                    font=("Segoe UI", 16))
            return
        if self.overlay_items:
            self._store_overlay()
        converter = find_pdftoppm()
        if pdfium is None and not converter:
            self.canvas.delete("all")
            self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                                    text="Visualizador indisponível.\nAs demais ferramentas continuam funcionando.",
                                    fill=MUTED, justify="center", font=("Segoe UI", 14))
            return
        try:
            if pdfium is not None:
                document = pdfium.PdfDocument(str(self.pdf_path))
                pdf_page = document[self.current_page]
                bitmap = pdf_page.render(scale=110 / 72)
                image = bitmap.to_pil().convert("RGB")
                pdf_page.close()
                document.close()
            else:
                with tempfile.TemporaryDirectory() as temp:
                    prefix = str(Path(temp) / "page")
                    subprocess.run([converter, "-f", str(self.current_page + 1), "-l", str(self.current_page + 1),
                                    "-singlefile", "-png", "-r", "110", str(self.pdf_path), prefix],
                                   check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    image = Image.open(prefix + ".png").convert("RGB")
            max_w = max(200, self.canvas.winfo_width() - 50)
            max_h = max(200, self.canvas.winfo_height() - 40)
            image.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
            self.page_image_size = image.size
            self.page_photo = ImageTk.PhotoImage(image)
            self.canvas.delete("all")
            cx, cy = self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2
            x0, y0 = cx - image.width // 2, cy - image.height // 2
            self.page_canvas_box = (x0, y0, x0 + image.width, y0 + image.height)
            self.canvas.create_rectangle(x0 - 3, y0 - 3, x0 + image.width + 3, y0 + image.height + 3,
                                         fill="#28364c", outline="")
            self.canvas.create_image(cx, cy, image=self.page_photo)
            self.page_var.set(f"Página {self.current_page + 1} de {len(self.reader.pages)}")
            self._draw_existing_placement()
        except Exception as exc:
            self.set_status(f"Falha ao renderizar a página: {exc}")

    def _draw_existing_placement(self):
        self.active_overlay = None
        self.overlay_items.clear()
        self.selection_box = None
        self.resize_handle = None
        for p in self.placements:
            if p.page == self.current_page:
                x0, y0, x1, y1 = self.page_canvas_box
                page = self.reader.pages[self.current_page]
                pw, ph = float(page.mediabox.width), float(page.mediabox.height)
                cx = x0 + p.x / pw * (x1 - x0)
                cy = y1 - (p.y + p.height) / ph * (y1 - y0)
                width = p.width / pw * (x1 - x0)
                self._create_overlay(Path(p.signature), cx, cy, width, select=False)

    def previous_page(self):
        if self.reader and self.current_page > 0:
            self._store_overlay()
            self.current_page -= 1
            self.render_page()

    def next_page(self):
        if self.reader and self.current_page < len(self.reader.pages) - 1:
            self._store_overlay()
            self.current_page += 1
            self.render_page()

    def refresh_signatures(self):
        self.signature_files = sorted((app_data_dir() / "signatures").glob("*.png"))
        self.signature_list.delete(0, tk.END)
        for path in self.signature_files:
            self.signature_list.insert(tk.END, path.stem)

    def import_signature(self):
        name = filedialog.askopenfilename(title="Importar assinatura",
                                          filetypes=[("Imagens", "*.png *.jpg *.jpeg *.webp *.bmp"), ("Todos", "*.*")])
        if not name:
            return
        try:
            img = Image.open(name).convert("RGBA")
            # Remove fundo quase branco para que a assinatura se integre ao PDF.
            pixels = img.load()
            for y in range(img.height):
                for x in range(img.width):
                    r, g, b, a = pixels[x, y]
                    if r > 238 and g > 238 and b > 238:
                        pixels[x, y] = (r, g, b, 0)
            bbox = img.getbbox()
            if bbox:
                img = img.crop(bbox)
            dest = app_data_dir() / "signatures" / f"assinatura_{len(list((app_data_dir() / 'signatures').glob('*.png'))) + 1}.png"
            img.save(dest, "PNG")
            self.refresh_signatures()
            self.set_status("Assinatura importada e armazenada localmente.")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Imagem inválida.\n\n{exc}")

    def draw_signature(self):
        dest = app_data_dir() / "signatures" / f"desenhada_{len(list((app_data_dir() / 'signatures').glob('*.png'))) + 1}.png"
        dialog = DrawSignatureDialog(self, dest)
        self.wait_window(dialog)
        if dialog.saved:
            self.refresh_signatures()
            self.set_status("Assinatura desenhada e armazenada localmente.")

    def select_signature(self, _event=None):
        selection = self.signature_list.curselection()
        if selection:
            self.active_signature = self.signature_files[selection[0]]

    def delete_signature(self):
        selection = self.signature_list.curselection()
        if not selection:
            return
        path = self.signature_files[selection[0]]
        if messagebox.askyesno(APP_NAME, f"Excluir '{path.stem}' do computador?"):
            path.unlink(missing_ok=True)
            self.active_signature = None
            self.refresh_signatures()

    def add_signature(self):
        if not self.reader:
            messagebox.showinfo(APP_NAME, "Abra um PDF primeiro.")
            return
        if not self.active_signature:
            messagebox.showinfo(APP_NAME, "Importe, desenhe ou selecione uma assinatura.")
            return
        x0, y0, x1, y1 = self.page_canvas_box
        width = (x1 - x0) * self.signature_scale.get() / 100
        count = len(self.overlay_items)
        offset = (count % 6) * 14
        self._create_overlay(self.active_signature, x0 + (x1 - x0 - width) / 2 + offset,
                             y0 + (y1 - y0) * .68 + offset, width)
        self.set_status(f"Assinatura adicionada ({len(self.overlay_items)} nesta página). Arraste para posicionar.")

    def _create_overlay(self, path: Path, x: float, y: float, width: float, select=True):
        image = Image.open(path).convert("RGBA")
        ratio = image.height / max(1, image.width)
        width = max(45, width)
        image.thumbnail((int(width), max(12, int(width * ratio))), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image)
        item = self.canvas.create_image(x, y, image=photo, anchor="nw", tags="signature")
        self.overlay_items[item] = {"path": Path(path), "photo": photo}
        if select:
            self.select_overlay(item)
        self.canvas.tag_bind("signature", "<Button-1>", self.overlay_click)
        self.canvas.tag_bind("signature", "<B1-Motion>", self.canvas_drag)
        self.canvas.focus_set()
        return item

    def overlay_click(self, event):
        current = self.canvas.find_withtag("current")
        if current and current[0] in self.overlay_items:
            self.select_overlay(current[0])
            coords = self.canvas.coords(self.active_overlay)
            self.drag_offset = (event.x - coords[0], event.y - coords[1])
            self.set_status("Assinatura selecionada. Arraste-a ou use a alça laranja para redimensionar.")

    def canvas_click(self, event):
        current = self.canvas.find_withtag("current")
        if current and current[0] == self.resize_handle:
            return
        if current and current[0] in self.overlay_items:
            self.select_overlay(current[0])
            bbox = self.canvas.bbox(self.active_overlay)
            self.drag_offset = (event.x - bbox[0], event.y - bbox[1])
        else:
            self.active_overlay = None
            self.clear_selection()

    def canvas_drag(self, event):
        if self.resizing:
            self.perform_resize(event)
            return
        if not self.active_overlay:
            return
        x0, y0, x1, y1 = self.page_canvas_box
        bbox = self.canvas.bbox(self.active_overlay)
        if not bbox:
            return
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        nx = min(max(x0, event.x - self.drag_offset[0]), x1 - w)
        ny = min(max(y0, event.y - self.drag_offset[1]), y1 - h)
        self.canvas.coords(self.active_overlay, nx, ny)
        self.show_selection()

    def select_overlay(self, item):
        if item not in self.overlay_items:
            return
        self.active_overlay = item
        self.canvas.tag_raise(item)
        self.show_selection()
        self.canvas.focus_set()

    def clear_selection(self):
        for item in (self.selection_box, self.resize_handle):
            if item:
                self.canvas.delete(item)
        self.selection_box = None
        self.resize_handle = None

    def show_selection(self):
        self.clear_selection()
        if not self.active_overlay or self.active_overlay not in self.overlay_items:
            return
        bbox = self.canvas.bbox(self.active_overlay)
        if not bbox:
            return
        x0, y0, x1, y1 = bbox
        self.selection_box = self.canvas.create_rectangle(
            x0 - 2, y0 - 2, x1 + 2, y1 + 2, outline=ACCENT, width=2,
            dash=(5, 3), tags="signature_selection"
        )
        size = 7
        self.resize_handle = self.canvas.create_rectangle(
            x1 - size, y1 - size, x1 + size, y1 + size,
            fill=ACCENT, outline="white", width=1, tags="resize_handle"
        )
        self.canvas.tag_bind("resize_handle", "<Button-1>", self.start_resize)
        self.canvas.tag_bind("resize_handle", "<B1-Motion>", self.perform_resize)
        self.canvas.tag_bind("resize_handle", "<ButtonRelease-1>", self.finish_resize)

    def start_resize(self, event):
        if not self.active_overlay or self.active_overlay not in self.overlay_items:
            return "break"
        bbox = self.canvas.bbox(self.active_overlay)
        self.resize_origin = (bbox[0], bbox[1])
        self.resize_source = Image.open(self.overlay_items[self.active_overlay]["path"]).convert("RGBA")
        self.resizing = True
        return "break"

    def perform_resize(self, event):
        if not self.resizing or not self.active_overlay or self.resize_source is None:
            return "break"
        page_x0, page_y0, page_x1, page_y1 = self.page_canvas_box
        x0, y0 = self.resize_origin
        ratio = self.resize_source.height / max(1, self.resize_source.width)
        width = max(45, min(event.x - x0, page_x1 - x0))
        height = max(12, width * ratio)
        if y0 + height > page_y1:
            height = page_y1 - y0
            width = max(45, height / max(ratio, .001))
        image = self.resize_source.copy()
        image.thumbnail((max(45, int(width)), max(12, int(height))), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image)
        self.overlay_items[self.active_overlay]["photo"] = photo
        self.canvas.itemconfigure(self.active_overlay, image=photo)
        self.canvas.coords(self.active_overlay, max(page_x0, x0), max(page_y0, y0))
        self.show_selection()
        return "break"

    def finish_resize(self, _event=None):
        if self.resizing:
            self.resizing = False
            if self.resize_source is not None:
                self.resize_source.close()
            self.resize_source = None
            self.show_selection()
            self.set_status("Assinatura redimensionada. O tamanho será preservado ao salvar o PDF.")

    def resize_overlay(self):
        if not self.active_overlay or self.active_overlay not in self.overlay_items:
            return
        x, y = self.canvas.coords(self.active_overlay)
        x0, _, x1, _ = self.page_canvas_box
        data = self.overlay_items[self.active_overlay]
        image = Image.open(data["path"]).convert("RGBA")
        ratio = image.height / max(1, image.width)
        width = (x1 - x0) * self.signature_scale.get() / 100
        image.thumbnail((max(45, int(width)), max(12, int(width * ratio))), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image)
        data["photo"] = photo
        self.canvas.itemconfigure(self.active_overlay, image=photo)
        self.canvas.coords(self.active_overlay, x, y)
        self.show_selection()

    def remove_overlay(self, store=False):
        if store:
            self._store_overlay()
        if self.active_overlay and self.active_overlay in self.overlay_items:
            self.canvas.delete(self.active_overlay)
            del self.overlay_items[self.active_overlay]
            self.set_status(f"Assinatura removida. {len(self.overlay_items)} restante(s) nesta página.")
        self.active_overlay = None
        self.clear_selection()

    def _store_overlay(self):
        if not self.reader:
            return
        x0, y0, x1, y1 = self.page_canvas_box
        page = self.reader.pages[self.current_page]
        pw, ph = float(page.mediabox.width), float(page.mediabox.height)
        self.placements = [p for p in self.placements if p.page != self.current_page]
        for item, data in self.overlay_items.items():
            bbox = self.canvas.bbox(item)
            if not bbox:
                continue
            px = (bbox[0] - x0) / (x1 - x0) * pw
            width = (bbox[2] - bbox[0]) / (x1 - x0) * pw
            height = (bbox[3] - bbox[1]) / (y1 - y0) * ph
            py = (y1 - bbox[3]) / (y1 - y0) * ph
            self.placements.append(Placement(self.current_page, str(data["path"]), px, py, width, height))

    def save_signed_pdf(self):
        if not self.reader or not self.pdf_path:
            messagebox.showinfo(APP_NAME, "Abra e assine um PDF primeiro.")
            return
        self._store_overlay()
        if not self.placements:
            messagebox.showinfo(APP_NAME, "Adicione uma assinatura ao documento.")
            return
        output = filedialog.asksaveasfilename(title="Salvar PDF assinado", defaultextension=".pdf",
                                              initialfile=f"{self.pdf_path.stem}_assinado.pdf",
                                              filetypes=[("Arquivo PDF", "*.pdf")])
        if not output:
            return
        def work():
            reader = PdfReader(str(self.pdf_path))
            writer = PdfWriter()
            for index, page in enumerate(reader.pages):
                items = [p for p in self.placements if p.page == index]
                if items:
                    packet = io.BytesIO()
                    w, h = float(page.mediabox.width), float(page.mediabox.height)
                    layer = pdf_canvas.Canvas(packet, pagesize=(w, h))
                    for p in items:
                        layer.drawImage(ImageReader(p.signature), p.x, p.y, p.width, p.height,
                                        preserveAspectRatio=True, mask="auto")
                    layer.save()
                    packet.seek(0)
                    page.merge_page(PdfReader(packet).pages[0])
                writer.add_page(page)
            with open(output, "wb") as fh:
                writer.write(fh)
            return f"PDF assinado salvo em: {output}"
        self.run_task("Salvando PDF assinado…", work)

    def merge_pdfs(self):
        names = filedialog.askopenfilenames(title="Selecione PDFs na ordem desejada",
                                            filetypes=[("Arquivos PDF", "*.pdf")])
        if len(names) < 2:
            return
        output = filedialog.asksaveasfilename(title="Salvar PDF unido", defaultextension=".pdf",
                                              initialfile="documentos_unidos.pdf", filetypes=[("PDF", "*.pdf")])
        if not output:
            return
        def work():
            writer = PdfWriter()
            for name in names:
                writer.append(name)
            with open(output, "wb") as fh:
                writer.write(fh)
            return f"{len(names)} PDFs unidos com sucesso: {output}"
        self.run_task("Unindo documentos…", work)

    def split_pdf(self):
        source = self.pdf_path
        if not source:
            name = filedialog.askopenfilename(title="Selecionar PDF", filetypes=[("PDF", "*.pdf")])
            source = Path(name) if name else None
        if not source:
            return
        try:
            total = len(PdfReader(str(source)).pages)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        interval_text = simpledialog.askstring(
            APP_NAME,
            f"O PDF possui {total} página(s).\n\n"
            "Quantas páginas cada arquivo deve conter?\n"
            "• Padrão: 1 (gera um arquivo por página)\n"
            "• Exemplo: 1-2 (gera blocos de duas páginas)\n"
            "• Exemplo: 1-3 (gera blocos de três páginas)",
            initialvalue="1", parent=self
        )
        if interval_text is None:
            return
        try:
            block_size = parse_split_block(interval_text, total)
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        folder = filedialog.askdirectory(title="Pasta para salvar as partes")
        if not folder:
            return
        def work():
            reader = PdfReader(str(source))
            created = 0
            for a, b in split_page_groups(total, block_size):
                writer = PdfWriter()
                for idx in range(a - 1, b):
                    writer.add_page(reader.pages[idx])
                suffix = f"pagina_{a}" if a == b else f"paginas_{a}-{b}"
                target = Path(folder) / f"{source.stem}_{suffix}.pdf"
                with open(target, "wb") as fh:
                    writer.write(fh)
                created += 1
            return f"{created} arquivo(s) criado(s), com até {block_size} página(s) cada, em: {folder}"
        self.run_task("Dividindo PDF…", work)

    def compress_pdf(self):
        source = self.pdf_path
        if not source:
            name = filedialog.askopenfilename(title="Selecionar PDF", filetypes=[("PDF", "*.pdf")])
            source = Path(name) if name else None
        if not source:
            return
        quality = simpledialog.askinteger(APP_NAME, "Nível de qualidade:\n1 = menor arquivo\n2 = equilibrado\n3 = melhor qualidade",
                                          initialvalue=2, minvalue=1, maxvalue=3, parent=self)
        if not quality:
            return
        output = filedialog.asksaveasfilename(title="Salvar PDF comprimido", defaultextension=".pdf",
                                              initialfile=f"{source.stem}_comprimido.pdf", filetypes=[("PDF", "*.pdf")])
        if not output:
            return
        converter = find_pdftoppm()
        if pdfium is None and not converter:
            messagebox.showerror(APP_NAME, "O componente de conversão necessário não foi encontrado.")
            return
        dpi, jpg_quality = {1: (90, 55), 2: (120, 72), 3: (150, 85)}[quality]
        def work():
            if pdfium is not None:
                document = pdfium.PdfDocument(str(source))
                images = []
                for index in range(len(document)):
                    page = document[index]
                    images.append(page.render(scale=dpi / 72).to_pil().convert("RGB"))
                    page.close()
                document.close()
            else:
                with tempfile.TemporaryDirectory() as temp:
                    prefix = str(Path(temp) / "page")
                    subprocess.run([converter, "-jpeg", "-r", str(dpi), "-jpegopt", f"quality={jpg_quality}",
                                    str(source), prefix], check=True, capture_output=True,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    files = sorted(Path(temp).glob("page-*.jpg"), key=lambda p: int(p.stem.split("-")[-1]))
                    images = [Image.open(p).convert("RGB") for p in files]
            if not images:
                raise RuntimeError("Nenhuma página foi processada.")
            images[0].save(output, "PDF", save_all=True, append_images=images[1:], resolution=dpi,
                           quality=jpg_quality, optimize=True)
            for img in images:
                img.close()
            before, after = source.stat().st_size, Path(output).stat().st_size
            reduction = (1 - after / before) * 100 if before else 0
            return f"PDF comprimido salvo: {output} ({reduction:.1f}% de redução)"
        self.run_task("Comprimindo PDF — isso pode levar alguns minutos…", work)

    def on_close(self):
        self.destroy()


def packaged_self_test() -> int:
    """Valida os principais componentes sem abrir a interface."""
    with tempfile.TemporaryDirectory() as temp:
        sample = Path(temp) / "teste.pdf"
        layer = pdf_canvas.Canvas(str(sample))
        layer.drawString(72, 760, "PDF Local Studio")
        layer.save()
        if len(PdfReader(str(sample)).pages) != 1:
            return 2
        if pdfium is None:
            return 3
        document = pdfium.PdfDocument(str(sample))
        page = document[0]
        image = page.render(scale=1).to_pil()
        valid = image.width > 0 and image.height > 0
        image.close()
        page.close()
        document.close()
        if not valid:
            return 4
        if parse_split_block("1-2", 4) != 2:
            return 5
        if split_page_groups(4, 2) != [(1, 2), (3, 4)]:
            return 6
        if split_page_groups(4, 1) != [(1, 1), (2, 2), (3, 3), (4, 4)]:
            return 7
        return 0


def packaged_ui_self_test() -> int:
    """Valida múltiplas assinaturas e redimensionamento no canvas."""
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        sample = folder / "teste.pdf"
        signature = folder / "assinatura.png"
        layer = pdf_canvas.Canvas(str(sample))
        layer.drawString(72, 760, "Teste de interface")
        layer.save()
        sign_image = Image.new("RGBA", (320, 100), (255, 255, 255, 0))
        ImageDraw.Draw(sign_image).line((10, 70, 130, 20, 300, 65), fill=(20, 30, 50, 255), width=6)
        sign_image.save(signature)

        window = FoxPDFLocal()
        window.geometry("1000x700")
        window.update()
        window.pdf_path = sample
        window.reader = PdfReader(str(sample))
        window.current_page = 0
        window.render_page()
        window.active_signature = signature
        window.add_signature()
        window.add_signature()
        if len(window.overlay_items) != 2:
            window.destroy()
            return 11
        first = next(iter(window.overlay_items))
        window.select_overlay(first)
        before = window.canvas.bbox(first)
        event = type("MouseEvent", (), {"x": before[2], "y": before[3]})()
        window.start_resize(event)
        event.x += 60
        event.y += 20
        window.perform_resize(event)
        window.finish_resize(event)
        after = window.canvas.bbox(first)
        window._store_overlay()
        valid = len(window.placements) == 2 and (after[2] - after[0]) > (before[2] - before[0])
        window.destroy()
        return 0 if valid else 12


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(packaged_self_test())
    if "--ui-self-test" in sys.argv:
        raise SystemExit(packaged_ui_self_test())
    FoxPDFLocal().mainloop()
