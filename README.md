# PDF Local Studio

Aplicativo desktop para trabalhar com PDFs sem enviar documentos ou assinaturas para a internet.

Desenvolvido por **JDM**.

## Funcionalidades

- Visualização de documentos PDF;
- importação de assinaturas em PNG, JPG, JPEG, WEBP e BMP;
- criação de assinatura desenhada com o mouse;
- armazenamento local das assinaturas no perfil do usuário;
- posicionamento e redimensionamento com o mouse, além da aplicação visual da assinatura;
- repetição da mesma assinatura e combinação de várias assinaturas em uma ou mais páginas;
- união de vários PDFs;
- divisão sequencial em arquivos separados: uma página por arquivo por padrão, ou blocos como `1-2`, `1-3` etc.;
- compressão em três níveis de qualidade.

## Executável para Windows

Baixe o arquivo `PDF-Local-Studio.exe` na seção **Releases** do GitHub. Ele já inclui o Python, o PDFium e todas as bibliotecas necessárias. Basta clicar duas vezes no executável; não é necessário instalar dependências.

> O programa ainda não possui certificado comercial de assinatura de código. O Windows SmartScreen pode exibir um aviso para executáveis baixados da internet.

## Como executar pelo código-fonte

Clique duas vezes em `iniciar.cmd`.

Para executar em outro computador com Python 3.11 ou superior:

```powershell
python -m pip install -r requirements.txt
python app.py
```

O executável inclui o mecanismo PDFium necessário para visualização e compressão.

## Privacidade

As assinaturas são guardadas em `%LOCALAPPDATA%\PDFLocalStudio\signatures`. Elas podem ser removidas dentro do aplicativo. Os PDFs são processados localmente.

## Limites desta versão

- A assinatura é uma marca visual, não uma assinatura digital baseada em certificado ICP-Brasil.
- A compressão converte as páginas para imagens; textos continuam visíveis, mas deixam de ser selecionáveis no arquivo comprimido.
- PDFs protegidos por senha precisam ser desbloqueados antes do processamento.

## Observação jurídica

Este é um aplicativo independente. Não contém código, marcas ou elementos proprietários do Foxit PDF ou do PDF24.

## Licença

Distribuído sob a licença MIT. Consulte o arquivo `LICENSE`.
