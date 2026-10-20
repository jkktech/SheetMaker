@echo off
rem ==========================================================================
rem  Create desktop shortcut.bat
rem
rem  Run once. Puts a "SheetMaker" shortcut on your desktop that points at
rem  "Start SheetMaker.bat" in this folder, which always opens the newest
rem  version. Keep both files in your SheetMaker root folder.
rem ==========================================================================
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%Start SheetMaker.bat" (
    echo.
    echo  "Start SheetMaker.bat" is not in this folder:
    echo     %ROOT%
    echo  Put both files in your SheetMaker root folder and run this again.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$desk = [Environment]::GetFolderPath('Desktop'); $bat = Join-Path $env:ROOT 'Start SheetMaker.bat'; $s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desk 'SheetMaker.lnk')); $s.TargetPath = $env:ComSpec; $q = [char]34; $s.Arguments = '/c ' + $q + $q + $bat + $q + $q; $s.WorkingDirectory = $env:ROOT.TrimEnd('\'); $s.WindowStyle = 7; $s.Description = 'Opens the newest SheetMaker'; $py = Get-Command pyw.exe, py.exe -ErrorAction SilentlyContinue | Select-Object -First 1; if ($py) { $s.IconLocation = $py.Source + ',0' }; $s.Save()"

if errorlevel 1 (
    echo.
    echo  Could not create the shortcut. You can make one by hand:
    echo  right-click "Start SheetMaker.bat" ^> Send to ^> Desktop ^(create shortcut^).
    echo.
    pause
    exit /b 1
)
echo.
echo  Done: "SheetMaker" is on your desktop. You can also right-click it and
echo  choose "Pin to taskbar" or "Pin to Start".
echo.
pause
endlocal
