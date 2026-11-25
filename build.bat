@echo off
rem Build the Windows app from source (needs Python 3.10+)
py -m pip install -r requirements-dev.txt || exit /b 1
py -m PyInstaller --noconfirm --clean --windowed --name ECSRTool --collect-all playwright main.py || exit /b 1
echo.
echo Done: dist\ECSRTool\ECSRTool.exe
pause
