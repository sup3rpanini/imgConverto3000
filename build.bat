@echo off
rem Construit l'executable (PyInstaller) puis l'installateur Windows (Inno Setup).
rem Resultats : dist\ConvertisseurImages.exe et installer_output\ConvertisseurImages_Setup.exe
cd /d "%~dp0"

rem --collect-all : pillow-heif (lecture/ecriture HEIC) et tkinterdnd2 (glisser-deposer)
rem embarquent des bibliotheques natives que PyInstaller ne detecte pas seul.
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name ConvertisseurImages ^
    --version-file version.txt ^
    --collect-all pillow_heif ^
    --collect-all tkinterdnd2 ^
    app.py
if errorlevel 1 (
    echo Echec de la construction de l'executable.
    exit /b 1
)

set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo Inno Setup 6 introuvable : seul l'executable a ete construit.
    exit /b 1
)
"%ISCC%" installer.iss
