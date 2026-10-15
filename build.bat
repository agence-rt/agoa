@echo off
rem Construit AGOA-Launcher.exe (test local ; la publication se fait par GitHub Actions)
py -m pip install --upgrade pyinstaller
py -m PyInstaller --noconsole --onefile --icon icon.ico --add-data "catalog.json;." --add-data "icon.ico;." --name AGOA-Launcher agoa_launcher.py
pause
