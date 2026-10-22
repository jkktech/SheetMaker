SheetMaker 0.1.30.01
====================

Lays out print & cut designs on sheets and writes, for every sheet,
a PRINT pdf and a CUT pdf with registration marks (for LaserVision, or
LightBurn Print and Cut). Full manual with pictures: docs/MANUAL.md

SET UP (once)
  1. Unzip this folder anywhere.
  2. Install Python 3 from python.org (tick "Add python.exe to PATH"), then
     double-click "Install add-ons.bat".
  3. Put "Start SheetMaker.bat" and "Create desktop shortcut.bat" in your
     SheetMaker root folder (the one holding the SheetMaker-x.y.z folders).
     Run "Create desktop shortcut.bat" once. The shortcut always opens the
     newest version, so it never needs updating: just unzip new versions into
     the root folder (right in it, or one level down, both are found).
     Or use SheetMaker.exe (taskbar / Start pin): keep it in the root folder, or
     copy it to your computer. Copied, it asks once for "Start SheetMaker.bat":
     paste the folder path (\\server\share\SheetMaker or S:\SheetMaker) into the
     address bar or File name box, or browse to it. Or put a text file
     "SheetMaker folder.txt" next to the exe with that path on its first line.
     Hold Shift while starting it to choose again. Right-click > Pin to taskbar.
     Something not starting? Run  "Start SheetMaker.bat" console  from a
     Command Prompt to see the error.

USE
  1. Add files...  Pick your designs. Each design can be:
       - ONE file with art + cut line. The cut line is found by:
           * a magenta stroke (#ff00ff, RGB or CMYK 0/100/0/0), or
           * a layer or spot colour with "cut" in its name (Cut, CutContour, Thru-cut...)
         Both are changeable under "Sheet and spacing". Cut line colour can be a
         list (#ff00ff, #ff0000): each file uses the first one it has.
         "Cut colours..." sets the cut line of all orange designs at once: tick the
         colour that is the cut line (and add it to the list for next time).
       Print-ready exports with crop marks, registration targets, colour bars or a
       file-info line around the artboard are cleaned by themselves on loading.
       - TWO files: a print file + a cut-only file. Pick both together; they are
         paired by name (e.g. "sticks-1.pdf" + "sticks-cut.pdf"). Or select the
         design and use "Set cut file...". A cut artboard smaller than the print
         artboard (bleed added to the print) is centred on it. Select several designs (e.g. all pages
         of a multi-page print file) to give them all the same cut file at once; a
         cut file with one page per selected design can go page by page instead.
       - A BLANK: a cut-only file on its own (no matching print file). It is
         cut but not printed, and shares sheets with print & cut designs. A
         sheet of only blanks gets no print file and no dots: cut it directly.
     A PDF with several pages: each page becomes its own design ("name p1",
     "name p2", ...). A multi-page cut file pairs with the print file page by page.
     .ai files work if they were saved with "Create PDF Compatible File" ticked.
     A print file with no cut line is still added (shown in orange, "no cut
     line yet"); add its cut line any time before Arrange.
     If no cut line is found (or the wrong one), select the design and press
     "Pick cut line...": it lists every kind of line in the file by colour,
     thickness and layer. Tick the cut line or click it in the picture.
     Lines with the same colour but a different thickness or layer are kept
     apart, so a black cut line can sit next to black art outlines.
     Two prints on top of each other (e.g. an ornament: cut page, front art printed
     first and mirrored, back art printed on top): select the pages and press
     Layers... . Choose Cut / Print 1 (first) / Print 2 (on top) and Mirror for each
     page. Each sheet then gets _P1 (first) and _P2 (on top, with the dots)
     plus _C, all the same size: load P1 and P2 in RasterLink at the same position
     and join them. These designs always get sheets of their own.
     In the preview of such a sheet, "Two prints:" turns Print 1 / Print 2 on and
     off; "Print 2 at 50%" shows the top print see-through over Print 1 to check
     they line up and the mirroring is right.
  2. Double-click Qty to set how many. Double-click Spacing to give one design
     its own gap (blank = job default, 5 mm). With several rows selected, the
     value goes to all of them. Click Turn to stop a design from
     being turned (e.g. grain direction).
  3. Set the largest sheet (default 23 x 35 in). Arrange.
       - Full sheets of the largest size are filled first.
       - Whatever is left goes on the smallest custom sheet that holds it,
         rounded up to 0.25 in. It may be turned sideways (e.g. 29 x 21.5 in).
       - Several layouts are tried (interlocking, straight grid, rows lined
         up at the bottom, columns lined up on the left, free placement);
         the one needing the least material wins.
     Scrap sheets: "Scrap list..." keeps your leftover sheets, grouped by
     material (Clear, Frosted, Black, White): width, height, how many and a
     note (thickness...). Untick Use to keep a scrap back. Tick "Use scrap
     sheets first" (off each time SheetMaker starts), choose the job's
     Material next to the job name, and Arrange fills scraps of that
     material before any new sheet. When the rest fits on one scrap, only a
     strip of it is used (full width), so the remainder stays a clean
     rectangle. After saving you're asked (1) whether to take the used scraps
     off the list and (2) which leftover pieces to add (tick each one).
     Material Black also turns on white dots.
     "Trim line around each sheet" (on by default) adds a blue cut line around
     every sheet, last in the cut file: put blue on its own layer and cut it
     after the pieces to cut the used area out with clean edges.
  4. Check the preview: the print artwork with the cut lines on top
     (tick boxes turn either off). The colour squares change the background
     behind the artwork (White, Black, Gray, Red; preview only) to check
     white ink and text. Scroll to zoom, drag to move, double-click to fit.
     Then "Save print + cut files...". A folder
     "<job> sheets" is made with, per sheet:
         <job>_1o2_<W>x<H>_P.pdf   - print: send to the printer (actual size, 100%)
         <job>_1o2_<W>x<H>_C.pdf   - cut: open in LaserVision
     ("1o2" = sheet 1 of 2, size in inches, e.g. myjob_1o2_23x35_P.pdf)
     plus <job>_summary.txt: per sheet MATERIAL, SCRAP, LEFTOVER (Keep / Trash,
     as answered after saving), PIECES and the PRINT / CUT file names.
     Tick "Save preview pictures" (off each time SheetMaker starts) to also
     get a "previews" subfolder with <job>_1o2_<W>x<H>_preview.png
     (artwork + cut lines).

REGISTRATION MARKS
  4 solid dots, 0.25 in, one near each corner at a random spot that is
  different on every sheet. Each layout is checked so LaserVision can't mix up
  the sheet's orientation. "New mark spots" re-arranges with new spots.
  Dots are black; a dot that lands on dark print is made white. Tick
  "All dots white (dark material)" to make every dot (and the file name) white.
  White is written as CMYK 0/0/1/0 (1% yellow) so the printer prints white ink
  instead of treating it as background.
  The cut pdf always has black dots (LaserVision reads only their position).

FILE NAME ON THE SHEET
  The print pdf carries its own name (e.g. myjob_1o2_23x35)
  in black 10 pt text, in the free strip closest to an edge: bottom first, then
  top, left, right (sideways on the sides). It keeps 2 mm from any artwork and
  bleed and 5 mm from the marks. It is not in the cut pdf, so LaserVision never
  sees it. If no edge has room, it is left off and you get a message on save.

GOOD TO KNOW
  - Spacing is measured cut line to cut line. Each copy's print art is trimmed
    to its cut shape plus half the spacing, so bleed never runs onto a neighbour.
  - Print at 100% / actual size, no "fit to page".
  - Settings are kept in sheetmaker_settings.json next to "Start SheetMaker.bat" (shared by all versions).
  - Rest the mouse on a design's name to see a picture of it (artwork + cut line).

SEVERAL COMPUTERS
  With SheetMaker on a network drive every computer uses the same
  sheetmaker_settings.json. Each SheetMaker checks it every 2.5 seconds, so a
  scrap added or used on one computer shows up on the others a few seconds later
  (also in an open Scrap list window). Saving only writes what changed on that
  computer, so nothing another computer did is lost. If two computers change the
  same scrap before seeing each other's change: a delete wins over an edit; the
  same field edited on both, the last save wins; "How many" changes add up.
  If the drive can't be reached, a red warning shows next to the scrap count and
  changes are saved once it is back. Each computer keeps its own last-used folder.
