@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" goto run
if "%NO_INSTALL%"=="1" goto nopy
where uv >nul 2>nul
if errorlevel 1 goto nopy
echo [KnowledgeLib] .venv not found, running uv sync...
uv sync
if errorlevel 1 goto fail
:run
"%PY%" "%~dp0app.py" %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
:nopy
echo [KnowledgeLib] Missing .venv. Install uv and run: uv sync
endlocal & exit /b 1
:fail
echo [KnowledgeLib] uv sync failed. Try: uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple
endlocal & exit /b 1
