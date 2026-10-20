# SheetMaker

**Print & cut sheet layout for laser cutters.** Drop in your designs, set how many of each you need, and
SheetMaker nests them on as little material as possible, then writes a **print PDF** and a matching
**cut PDF** for every sheet, with registration dots so the laser can find the printed pieces.

![SheetMaker main window](docs/images/main-window.png)

Built for shops that print on a flatbed / UV printer and cut on a laser. Works with
[**LaserVision**](https://github.com/jkktech/laservision) (camera registration: it finds the dots by itself)
and with **LightBurn's Print and Cut** (use two of the dots as targets).

## What it does

- **Reads your files as they are.** PDF or PDF-compatible Illustrator (.ai). One file with art + cut
  line (magenta stroke, or a layer / spot colour called *Cut*, *CutContour*, *Thru-cut*…), a print file
  + cut file pair (paired by name), or a cut-only *blank*. Multi-page PDFs load one design per page.
- **Finds the cut line, or lets you pick it.** *Pick cut line…* lists every kind of line in the file by
  colour, thickness and layer; tick it or click it in the picture.
- **True-shape nesting.** Pieces interlock by their real cut outline, not their bounding box, with your
  spacing (per design if needed) and optional 90° turns. Several layouts are tried and the one using the
  least material wins.
- **Smart sheet sizes.** Full sheets of your largest size first, then the leftover pieces on the
  smallest custom sheet that holds them (rounded to 0.25 in).
- **Scrap sheets first.** Keep a list of leftover material by type (Clear, White, Black…). SheetMaker
  fills matching scraps before new sheets and offers to add the new leftovers back to the list.
- **Registration dots.** Four 0.25 in dots near the corners, at random spots on every sheet, checked so
  the sheet can't be read the wrong way round. Black, or white ink (CMYK 0/0/1/0) on dark print or dark
  material. Optional blue trim line to cut the used area off the sheet.
- **Two prints on one piece.** For ornaments and other two-sided work printed in two passes:
  *Layers…* sets which page is the cut, Print 1 (first) and Print 2 (on top), and mirrors pages in place.
  You get `_P1`, `_P2` and `_C` files that line up exactly, and a preview that shows Print 2 at 50% over
  Print 1 to check alignment and mirroring.
- **Clean output.** Per sheet: print PDF(s) at 100 % size, cut PDF, the file name printed on the sheet
  edge, a summary .txt (material, scrap used, leftover, pieces, file names) and optional preview pictures.
- **Several computers, one setup.** Keep SheetMaker on a network drive: every computer shares one
  settings file, and scrap-list changes show up on the others within seconds.

| Print and cut files | Two prints, checked at 50 % |
|---|---|
| ![Print and cut PDF](docs/images/output-print-cut.png) | ![Two-print preview](docs/images/two-prints-50.png) |

## Install (Windows)

1. Install **Python 3** from [python.org](https://www.python.org/downloads/) and tick
   *Add python.exe to PATH*.
2. Download this repository (**Code → Download ZIP**) and unzip it, e.g. to `C:\SheetMaker` or a
   network drive.
3. Double-click **`Install add-ons.bat`** (installs pymupdf, numpy, opencv-python, pikepdf).
4. Double-click **`Start SheetMaker.bat`**, or run `Create desktop shortcut.bat` once.
   `SheetMaker.exe` can be pinned to the taskbar.

New versions: unzip the new `SheetMaker-…` folder next to the old ones; the start file always opens the
newest, and your settings are kept.

## Quick start

1. **Add files…** and pick your designs (try the ones in [`docs/samples`](docs/samples)).
2. Double-click **Qty** to set how many of each.
3. Set the **largest sheet** and press **Arrange**.
4. Check the preview, then **Save print + cut files…**.
5. Print the `_P` file at 100 %, put the printed sheet in the laser and cut the `_C` file with
   LaserVision or LightBurn Print and Cut.

The full guide, with every option and both cutting workflows, is in the **[manual](docs/MANUAL.md)**.

## Repository layout

- `SheetMaker-a.b.cc.dd/` — the program (with its CHANGELOG.md)
- `Start SheetMaker.bat` — opens the newest `SheetMaker-…` folder it finds
- `SheetMaker.exe` — taskbar launcher for the .bat (source in `launcher/`)
- `Create desktop shortcut.bat`, `Install add-ons.bat`
- `docs/` — [manual](docs/MANUAL.md), screenshots and sample designs
- `README.txt` — short user guide that travels with the program

Current version: **0.1.28.01** (numbered a.b.cc.dd: a.b.cc = base version, dd = build).
