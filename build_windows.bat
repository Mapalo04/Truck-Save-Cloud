@echo off
setlocal
py -m pip install -r requirements.txt || exit /b 1
py -m PyInstaller --noconfirm --clean TruckSaveCloud.spec || exit /b 1
echo Build complete: dist\TruckSaveCloud\TruckSaveCloud.exe
echo Open installer\TruckSaveCloud.iss in Inno Setup Compiler to create installer.
