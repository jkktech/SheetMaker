@echo off
rem Installs (or repairs) the add-ons SheetMaker needs. Uses the same Python as LaserVision.
echo Installing SheetMaker add-ons: pymupdf, numpy, opencv-python, pikepdf ...
py -m pip install --upgrade pymupdf numpy opencv-python pikepdf
if errorlevel 1 (
    echo.
    echo Something went wrong. Is Python installed, and does "py --version" work in Command Prompt?
) else (
    echo.
    echo Done. You can now double-click "Start SheetMaker.bat".
)
pause
