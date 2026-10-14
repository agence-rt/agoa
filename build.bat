@echo off
rem Construit AGOA-Launcher.exe (une seule fois, sur un poste avec Python)
py -m pip install --upgrade pyinstaller
py -m PyInstaller --noconsole --onefile --add-data "catalog.json;." --name AGOA-Launcher agoa_launcher.py
copy /Y catalog.json dist\catalog.json
echo.
echo Termine : dist\AGOA-Launcher.exe  +  dist\catalog.json
pause
