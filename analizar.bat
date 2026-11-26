@echo off
REM Doble clic para analizar tu lista. Edita la ruta de MO2 la primera vez.
set MO2=C:\Modding\MO2
cd /d "%~dp0"
python -m autopatcher --mo2 "%MO2%" %*
pause
