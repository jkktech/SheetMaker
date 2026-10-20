# SheetMaker.exe launcher

Runs "Start SheetMaker.bat" from the SheetMaker root folder (its own folder, or the one remembered in
%LOCALAPPDATA%\SheetMaker\launcher_root.txt; Shift at start = pick again). Go standard library only.

Build (any OS with Go 1.21+ and Python 3):

    python make_syso.py sheetmaker.ico rsrc_windows_amd64.syso   # icon + version info + manifest
    GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags "-H windowsgui -s -w" -o SheetMaker.exe .

Copy SheetMaker.exe to the repository root (next to "Start SheetMaker.bat").
