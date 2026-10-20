# SheetMaker

Lays out print & cut designs on sheets and writes, for every sheet, a print PDF and a cut PDF with
registration dots for [LaserVision](https://github.com/jkktech/laservision).

## Install

1. Install Python 3 from python.org (tick "Add python.exe to PATH").
2. Download this repository (Code → Download ZIP) and unzip it, e.g. to `C:\SheetMaker` or a network drive.
3. Double-click `Install add-ons.bat` (pymupdf, numpy, opencv-python, pikepdf).
4. Double-click `Start SheetMaker.bat`, or run `Create desktop shortcut.bat` once. `SheetMaker.exe` can be pinned to the taskbar.

## Layout of this repository

- `SheetMaker-a.b.cc.dd/` — the program (newest version, with its CHANGELOG.md)
- `Start SheetMaker.bat` — opens the newest `SheetMaker-…` folder it finds
- `SheetMaker.exe` — taskbar launcher for the .bat (source in `launcher/`)
- `Create desktop shortcut.bat`, `Install add-ons.bat`
- `README.txt` — the full user guide

Current version: 0.1.28.01 (numbered a.b.cc.dd: a.b.cc = base version, dd = build).
