@echo off
REM Запуск импортёра из его собственной папки, чтобы сработал импорт database.py
REM (pars_to_db.py делает "from database import ...").
cd /d "%~dp0"

REM Укажите путь к python.exe этого сервера (или оставьте "python", если он в PATH).
set "PYTHON=python"

"%PYTHON%" pars_to_db.py
exit /b %ERRORLEVEL%
