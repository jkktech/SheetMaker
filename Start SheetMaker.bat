@echo off
rem ==========================================================================
rem  Start SheetMaker.bat
rem
rem  Put this file in your SheetMaker root folder (the folder that holds the
rem  SheetMaker-x.y.z version folders) and point your shortcut at it.
rem  It always opens the NEWEST version it finds, so the shortcut never needs
rem  updating: just unzip a new version into the root folder.
rem
rem  Version folders may sit right in the root, or one level down, e.g.
rem     SheetMaker\SheetMaker-0.1.3\
rem     SheetMaker\SheetMaker-0.1.4\SheetMaker-0.1.4\   (Windows "Extract All")
rem
rem  All versions share one settings file, sheetmaker_settings.json, kept
rem  next to this file.
rem
rem  Troubleshooting: run   "Start SheetMaker.bat" console
rem  to keep a window open that shows any error messages.
rem ==========================================================================
setlocal
rem full paths only (no "cd"), so a \\server\share folder works as well as a drive letter
set "ROOT=%~dp0"
set "LATEST="

for /f "usebackq delims=" %%D in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath $env:ROOT -Directory -Recurse -Depth 2 -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^SheetMaker-\d+(\.\d+)+$' -and (Test-Path -LiteralPath (Join-Path $_.FullName 'sheetmaker.py')) } | Sort-Object { [version]($_.Name.Substring(11)) } | Select-Object -Last 1 -ExpandProperty FullName"`) do set "LATEST=%%D"

if not defined LATEST (
    echo.
    echo  No SheetMaker-x.y.z folder found in:
    echo     %ROOT%
    echo.
    echo  Unzip a SheetMaker version into this folder first.
    echo.
    pause
    exit /b 1
)

rem ---- Python and add-ons -----------------------------------------------------
where py >nul 2>nul
if errorlevel 1 (
    echo.
    echo  Python was not found ^("py" command^). Install Python from python.org,
    echo  ticking "Add python.exe to PATH", then try again.
    echo.
    pause
    exit /b 1
)
py -c "import pymupdf, pikepdf, cv2, numpy" >nul 2>nul
if errorlevel 1 (
    echo.
    echo  SheetMaker needs a few add-ons that are not installed yet:
    echo     pymupdf, numpy, opencv-python, pikepdf
    echo.
    choice /c YN /m " Install them now"
    if errorlevel 2 exit /b 1
    py -m pip install --upgrade pymupdf numpy opencv-python pikepdf
    if errorlevel 1 (
        echo.
        echo  Installing failed. See the messages above.
        pause
        exit /b 1
    )
)

rem ---- Start ---------------------------------------------------------------------
set "SHEETMAKER_SETTINGS=%ROOT%sheetmaker_settings.json"

if /i "%~1"=="console" (
    echo Starting %LATEST%
    py "%LATEST%\sheetmaker.py"
    pause
    exit /b
)

rem pyw runs without a black console window; fall back to py if it's missing
where pyw >nul 2>nul
if errorlevel 1 (
    start "SheetMaker" /min /D "%LATEST%" py "%LATEST%\sheetmaker.py"
) else (
    start "" /D "%LATEST%" pyw "%LATEST%\sheetmaker.py"
)
endlocal
