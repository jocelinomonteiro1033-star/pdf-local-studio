@echo off
setlocal
set "RUNTIME_PY=C:\Users\JDM\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
if exist "%RUNTIME_PY%" (
  "%RUNTIME_PY%" "%~dp0app.py"
  exit /b
)
where pythonw >nul 2>nul
if %errorlevel% equ 0 (
  pythonw "%~dp0app.py"
  exit /b
)
echo Python nao foi encontrado. Consulte o arquivo README.md.
pause
