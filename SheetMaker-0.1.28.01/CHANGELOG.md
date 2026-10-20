# SheetMaker changelog

Versions are numbered `a.b.cc.dd`: `a.b.cc` is the base version, `dd` counts its builds (0.1.28.01, 0.1.28.02 …). Each version is a folder `SheetMaker-a.b.cc.dd`. To update, unzip the new version into your SheetMaker root folder; "Start SheetMaker.bat" (and the desktop shortcut / SheetMaker.exe) always opens the newest one, and every version shares one `sheetmaker_settings.json`.

## 0.1.28.01 — 2026-10-20 (first public release)
### Features
- **Designs:** PDF or PDF-compatible .ai. One file with art + cut line (magenta stroke, or a layer / spot colour named "cut"), a print file + cut file pair (paired by name), or a cut-only blank. Multi-page PDFs load one design per page.
- **Pick cut line…** lists every kind of line in a file (colour, thickness, layer) to choose the cut line by hand. **Set cut file…** gives one or several selected designs the same cut file (or page by page).
- **Layers…** for two-print designs (e.g. ornaments: cut page, Print 1 printed first, Print 2 on top), each page mirrorable. These sheets get `_P1`, `_P2` and `_C` files at the same size; the preview can show each print, or Print 2 at 50% over Print 1.
- **Arrange:** true-shape nesting with per-design quantity, spacing and turning; full sheets of the largest size first, then the smallest custom sheet that fits; several layouts tried, the one with least material wins.
- **Scrap sheets:** a scrap list by material, used first when ticked; leftovers can be added back after saving.
- **Registration marks** for LaserVision: 4 dots at random spots per sheet, checked so the sheet can't be read the wrong way round; black, or white (CMYK 0/0/1/0) on dark print / dark material. Optional blue trim line around each sheet. File name printed on the sheet edge.
- **Output per sheet:** print PDF(s), cut PDF, a summary .txt (material, scrap, leftover, pieces, file names) and optional preview pictures.
- **Several computers:** one shared settings file on a network drive, kept in sync every few seconds.
- **SheetMaker.exe** launcher to pin to the taskbar (source in `launcher/`).
