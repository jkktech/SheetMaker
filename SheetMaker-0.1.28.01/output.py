"""
output.py - Write each sheet as a PRINT pdf and a CUT pdf, plus a preview picture.

Print pdf: the original vector art of every copy (cut line removed), placed and
turned, and trimmed to its cut line plus bleed (half the spacing), plus the
registration marks as solid 0.25 in dots: black, or white where the dot would sit on
dark print or when all dots are set to white (dark material).
Cut pdf:   the same page size, the same marks (solid black dots) and every cut line
as a thin stroke in its original colour. This is exactly what LaserVision reads:
filled round dots = marks, stroked paths = cut lines.
"""

import copy
import math
import os
import re

import cv2
import numpy as np
import pymupdf

from nest import MARK_DIAMETER_MM, apply

MM_TO_PT = 72.0 / 25.4
CUT_LINE_WIDTH_PT = 0.25
TRIM_RGB = (0.0, 0.0, 1.0)            # trim line around the sheet: blue, its own layer in LaserVision
TRIM_PREVIEW_BGR = (255, 90, 0)


def _rgb(hexcol):
    h = hexcol.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _num(v):
    """23.0 -> '23', 12.75 -> '12.75', 8.5 -> '8.5'."""
    return f"{v:.2f}".rstrip("0").rstrip(".")


def sheet_label(sheet):
    """Sheet size in inches for file names, e.g. '23x35' or '12.75x25.75'."""
    return f"{_num(sheet.W / 25.4)}x{_num(sheet.H / 25.4)}"


PREVIEW_DIR = "previews"      # preview pictures go in this subfolder of the save folder


def file_names(job, i, n, sheet, ver=1):
    base = sheet_base(job, i, n, sheet, ver)
    return base + "_P.pdf", base + "_C.pdf", base + "_preview.png"


def is_layered_sheet(sheet):
    """A sheet of two-print designs (Print 1 first, Print 2 on top): two print files."""
    return any(getattr(d, "layered", False) for d, _, _ in sheet.placements)


def under_sheet(sheet):
    """Print 1 of a two-print sheet: the same sheet with each two-print design's Print 1 page,
    without dots or file name."""
    under = copy.copy(sheet)
    under.placements = [(_under(d) if getattr(d, "layered", False) else d, q, A)
                        for d, q, A in sheet.placements]
    under.marks, under.label = [], None
    return under


def print_names(job, i, n, sheet, ver=1):
    """[(label, file name)] of the sheet's print files: one (_P), two (_P1 first, _P2 on top) or none."""
    if blanks_only(sheet):
        return []
    base = sheet_base(job, i, n, sheet, ver)
    if is_layered_sheet(sheet):
        return [("PRINT 1", base + "_P1.pdf"), ("PRINT 2", base + "_P2.pdf")]
    return [("PRINT", base + "_P.pdf")]


def _under(d):
    """Stand-in for a two-print design that prints its Print 1 page (same place, same cut)."""
    u = d.__dict__.get("_under_proxy")
    if u is None:
        u = copy.copy(d)
        u.print_pdf = d.under_pdf
        u.__dict__.pop("_under_proxy", None)
        d._under_proxy = u
    return u


def sheet_base(job, i, n, sheet, ver=1):
    """'myjob_1o2_23x35': job, sheet 1 of 2, size in inches ('_v2' etc. when saved again
    into a folder that already has that name). Also printed on the sheet."""
    return f"{job}_{i}o{n}_{sheet_label(sheet)}" + (f"_v{ver}" if ver > 1 else "")


def _quarter_turns(A):
    """0..3 quarter turns (counter-clockwise on screen) in a placement transform."""
    a, b = A[0, 0], A[0, 1]
    if a > 0.5:
        return 0
    if b > 0.5:
        return 1          # (x, y) -> (y, ...)
    if a < -0.5:
        return 2
    return 3


# White dots / text: CMYK with 1% yellow, not plain white. The printer treats pure white
# as "no ink / background" and leaves it out; with a trace of yellow it prints white ink.
WHITE_INK = (0.0, 0.0, 0.01, 0.0)          # C, M, Y, K

DARK_LUMINANCE = 0.5          # print darker than this under / around a dot -> white dot
MARK_LOOK_RING_MM = 1.5       # look this far past the dot's edge when judging the colour under it


def _draw_marks(page, marks, colors=None):
    r = MARK_DIAMETER_MM / 2 * MM_TO_PT
    for i, (mx, my) in enumerate(marks):
        fill = WHITE_INK if colors and colors[i] == "white" else (0, 0, 0)
        page.draw_circle(pymupdf.Point(mx * MM_TO_PT, my * MM_TO_PT), r, color=None, fill=fill, width=0)


def _mark_colors(page, sheet, mode, spacing_of=None):
    """'black' or 'white' per mark. mode 'white' = all white (dark material);
    'auto' = white only where the print around the dot is dark. Art only exists inside
    each copy's cut line + bleed, so a mark with no copy that close needs no look at all;
    otherwise the page is drawn once and only the spots around those marks are checked."""
    if mode == "white":
        return ["white"] * len(sheet.marks)
    R = MARK_DIAMETER_MM / 2 + MARK_LOOK_RING_MM
    near = []
    for mx, my in sheet.marks:
        hit = False
        for d, q, A in sheet.placements:
            reach = R + (spacing_of(d) / 2 if spacing_of else 10.0) + 0.5
            pts = np.vstack([apply(A, p) for p in d.cut])
            x0, y0 = pts.min(0) - reach
            x1, y1 = pts.max(0) + reach
            if x0 <= mx <= x1 and y0 <= my <= y1:
                hit = True
                break
        near.append(hit)
    out = ["black"] * len(sheet.marks)
    if not any(near):
        return out
    ppm = 8.0                                   # pixels per mm for the check
    m = pymupdf.Matrix(ppm / MM_TO_PT, ppm / MM_TO_PT)
    dl = page.get_displaylist()
    for i, (mx, my) in enumerate(sheet.marks):
        if not near[i]:
            continue
        clip = pymupdf.Rect((mx - R) * MM_TO_PT, (my - R) * MM_TO_PT, (mx + R) * MM_TO_PT, (my + R) * MM_TO_PT)
        pix = dl.get_pixmap(matrix=m, clip=clip, alpha=False, colorspace=pymupdf.csGRAY)
        g = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).astype(float) / 255
        yy, xx = np.mgrid[0:pix.height, 0:pix.width]
        cy, cx = (pix.height - 1) / 2, (pix.width - 1) / 2
        disk = (yy - cy) ** 2 + (xx - cx) ** 2 <= (R * ppm) ** 2
        lum = float(np.median(g[disk])) if disk.any() else 1.0
        out[i] = "white" if lum < DARK_LUMINANCE else "black"
    return out


# ------------------------------------------------------------------ trimming art to its shape

def outline_polys(d, bleed, max_px=4000):
    """Closed polygons (design mm) around the design's cut line, grown by `bleed`.
    The print art of each copy is trimmed to these, so artwork outside the cut line
    (e.g. a background rectangle around an oval) never prints onto marks or neighbours."""
    cache = d.__dict__.setdefault("_outline_cache", {})
    key = round(bleed, 3)
    if key in cache:
        return cache[key]
    x0, y0, x1, y1 = d.cut_box
    pad = bleed + 1.0
    res = max(0.05, max(x1 - x0 + 2 * pad, y1 - y0 + 2 * pad) / max_px)       # mm per pixel
    ox, oy = x0 - pad, y0 - pad
    w = int(math.ceil((x1 - x0 + 2 * pad) / res)) + 1
    h = int(math.ceil((y1 - y0 + 2 * pad) / res)) + 1
    M = np.zeros((h, w), np.uint8)
    for p in d.cut:
        pts = np.round((p - [ox, oy]) / res * 16).astype(np.int32)
        if math.dist(p[0], p[-1]) <= 0.2 and len(p) >= 3:
            cv2.fillPoly(M, [pts], 255, lineType=cv2.LINE_8, shift=4)
        cv2.polylines(M, [pts], False, 255, 1, lineType=cv2.LINE_8, shift=4)
    cnts, _ = cv2.findContours(M, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(M, cnts, -1, 255, thickness=cv2.FILLED)
    k = int(round(bleed / res))
    if k > 0:
        M = cv2.dilate(M, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1)))
    cnts, _ = cv2.findContours(M, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    polys = []
    for c in cnts:
        c = cv2.approxPolyDP(c, 0.6, True).reshape(-1, 2).astype(float)
        if len(c) >= 3:
            # pixel centres -> mm; +0.5 px so the outline sits on the outer pixel edge
            polys.append(np.column_stack([ox + (c[:, 0] + 0.5) * res, oy + (c[:, 1] + 0.5) * res]))
    cache[key] = polys
    return polys


def _clip_ops(polys_sheet_mm, page_h_pt):
    """PDF path operators for a clip path in page space (y up)."""
    out = []
    for p in polys_sheet_mm:
        pts = [(x * MM_TO_PT, page_h_pt - y * MM_TO_PT) for x, y in p]
        out.append("%.3f %.3f m" % pts[0])
        out += ["%.3f %.3f l" % q for q in pts[1:]]
        out.append("h")
    return "\n".join(out) + "\nW n\n"


LABEL_PT = 10                 # file name printed on a free edge of the print sheet
LABEL_FONT = "helv"
LABEL_CLEAR_MM = 2.0          # gap kept between the label and any artwork
LABEL_MARK_CLEAR_MM = 5.0     # ...and the registration marks, so the camera only sees the dot
LABEL_RES = 0.5               # mm per grid cell for the free-space search


def find_label_spot(sheet, text, spacing_of, edge_mm):
    """Where the label fits: the free strip closest to a sheet edge, trying the bottom,
    top, left and right edges in that order. Returns (x_mm, y_mm, rotate) for
    page.insert_text (baseline start point), or None if it fits nowhere."""
    tw = pymupdf.get_text_length(text, fontname=LABEL_FONT, fontsize=LABEL_PT) / MM_TO_PT
    font = pymupdf.Font(LABEL_FONT)
    th = (font.ascender - font.descender) * LABEL_PT / MM_TO_PT     # line height
    asc = font.ascender * LABEL_PT / MM_TO_PT                         # baseline below the top of the line
    r = LABEL_RES
    W, H = int(math.ceil(sheet.W / r)), int(math.ceil(sheet.H / r))
    occ = np.zeros((H, W), np.uint8)
    for d, q, A in sheet.placements:
        grow = spacing_of(d) / 2 + LABEL_CLEAR_MM            # art can run half the spacing past the cut
        k = max(1, int(math.ceil(grow / r)))
        m = np.zeros_like(occ)
        for p in d.cut:
            pts = np.round(apply(A, p) / r * 8).astype(np.int32)
            if math.dist(p[0], p[-1]) <= 0.2 and len(p) >= 3:
                cv2.fillPoly(m, [pts], 1, shift=3)
            cv2.polylines(m, [pts], False, 1, 1, shift=3)
        ys, xs = np.nonzero(m)
        if not len(ys):
            continue
        y0, y1 = max(0, ys.min() - k - 1), min(H, ys.max() + k + 2)
        x0, x1 = max(0, xs.min() - k - 1), min(W, xs.max() + k + 2)
        ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))
        occ[y0:y1, x0:x1] |= cv2.dilate(m[y0:y1, x0:x1], ker)
    for mx, my in sheet.marks:
        cv2.circle(occ, (int(mx / r), int(my / r)), int((MARK_DIAMETER_MM / 2 + LABEL_MARK_CLEAR_MM) / r) + 1, 1, -1)
    ii = cv2.integral(occ)                       # (H+1, W+1) sums

    def free(x0, y0, w, h):
        """Boxes in cells; returns bool array over all x0 for this row band."""
        return (ii[y0 + h, x0 + w] - ii[y0, x0 + w] - ii[y0 + h, x0] + ii[y0, x0]) == 0

    e = int(math.ceil(edge_mm / r))
    lw, lh = int(math.ceil(tw / r)) + 1, int(math.ceil(th / r)) + 1
    span = int(40 / r)                           # look up to 40 mm in from each edge
    for side in ("bottom", "top", "left", "right"):
        horiz = side in ("bottom", "top")
        bw, bh = (lw, lh) if horiz else (lh, lw)
        if bw + 2 * e > W or bh + 2 * e > H:
            continue
        for dd in range(e, e + span):
            if horiz:
                y = H - dd - bh if side == "bottom" else dd
                if y < e or y + bh > H - e:
                    break
                xs = np.arange(e, W - e - bw + 1)
                ok = free(xs, y, bw, bh)
                if ok.any():
                    x = int(xs[np.argmax(ok)])
                    return (x * r, (y * r) + asc, 0)
            else:
                x = dd if side == "left" else W - dd - bw
                if x < e or x + bw > W - e:
                    break
                ys = np.arange(e, H - e - bh + 1)
                ok = free(x, ys, bw, bh)
                if ok.any():
                    yy = int(ys[np.argmax(ok)])
                    # text reads bottom to top: baseline start at the box's bottom, ascent to the left
                    return (x * r + asc, (yy + bh) * r, 90)
    return None


def _draw_label(page, sheet, text, spacing_of, edge_mm, white=False):
    spot = find_label_spot(sheet, text, spacing_of, edge_mm)
    if spot is None:
        return False
    x, y, rot = spot
    page.insert_text(pymupdf.Point(x * MM_TO_PT, y * MM_TO_PT), text, fontname=LABEL_FONT, fontsize=LABEL_PT,
                     color=WHITE_INK if white else (0, 0, 0), rotate=rot)
    return True


class ScaleError(Exception):
    pass


def _matrix(doc, xref):
    t, v = doc.xref_get_key(xref, "Matrix")
    if t != "array":
        return np.eye(3)
    a = [float(x) for x in v.strip("[]").split()]
    return np.array([[a[0], a[1], 0], [a[2], a[3], 0], [a[4], a[5], 1]])


def _check_true_size(doc, page, d):
    """Safety net: every copy must be placed at exactly 100%. Reads back the transform
    the PDF library wrote for the copy just placed (its newest content stream
    ' q /fzFrmN Do Q ') and refuses to continue if the art would be scaled."""
    body = doc.xref_stream(page.get_contents()[-1]).decode("latin-1")
    m = re.search(r"/(fzFrm\d+)\s+Do", body)
    if not m:
        raise ScaleError(f"Could not check the size of '{d.name}'. Nothing was saved.")
    t, v = doc.xref_get_key(page.xref, f"Resources/XObject/{m.group(1)}")
    outer = int(v.split()[0])
    M = _matrix(doc, outer)
    t, v = doc.xref_get_key(outer, "Resources/XObject/fullpage")
    if t == "xref":
        M = _matrix(doc, int(v.split()[0])) @ M
    sx, sy = math.hypot(M[0, 0], M[0, 1]), math.hypot(M[1, 0], M[1, 1])
    if abs(sx - 1) > 0.001 or abs(sy - 1) > 0.001:
        raise ScaleError(f"'{d.name}' would be placed at {sx * 100:.1f}% x {sy * 100:.1f}% instead of 100%. "
                         "Nothing was saved. Please send this design to get it fixed.")


def blanks_only(sheet):
    """True if every piece on the sheet is a blank (cut only): no print file, no dots."""
    return bool(sheet.placements) and all(getattr(d, "blank", False) for d, _, _ in sheet.placements)


def build_print_doc(sheet, spacing_of, sources):
    """The print sheet as an in-memory PDF: every copy's art placed, turned and trimmed
    to its cut line + bleed, then the marks (black / white) and the file name."""
    doc = pymupdf.open()
    Hpt = sheet.H * MM_TO_PT
    page = doc.new_page(width=sheet.W * MM_TO_PT, height=Hpt)
    for d, q, A in sheet.placements:
        if getattr(d, "blank", False):
            continue                      # blank: cut only, nothing printed
        src = sources.get(id(d))
        if src is None:
            src = sources[id(d)] = pymupdf.open("pdf", d.print_pdf)
        bleed = spacing_of(d) / 2
        x0, y0, x1, y1 = d.clip_box(bleed)
        # Keep the grabbed area inside the design's own page. If the cut line runs to the
        # artboard edge, cut + bleed reaches past it; the PDF library would then shrink the
        # grab to the page but still fill the full target, enlarging the art (up to 13%).
        pw, ph = src[0].rect.width / MM_TO_PT, src[0].rect.height / MM_TO_PT
        x0, y0, x1, y1 = max(x0, 0.0), max(y0, 0.0), min(x1, pw), min(y1, ph)
        if x1 - x0 < 0.01 or y1 - y0 < 0.01:
            continue                      # art page doesn't overlap the cut area at all
        corners = apply(A, [(x0, y0), (x1, y0), (x0, y1), (x1, y1)])
        tx0, ty0 = corners.min(0)
        tx1, ty1 = corners.max(0)
        target = pymupdf.Rect(tx0 * MM_TO_PT, ty0 * MM_TO_PT, tx1 * MM_TO_PT, ty1 * MM_TO_PT)
        clip = pymupdf.Rect(x0 * MM_TO_PT, y0 * MM_TO_PT, x1 * MM_TO_PT, y1 * MM_TO_PT)
        turns = _quarter_turns(A)
        page.show_pdf_page(target, src, 0, clip=clip, rotate=ROTATE_SIGN * 90 * turns, keep_proportion=False)
        _check_true_size(doc, page, d)
        # each placement is its own content stream " q /fzFrmN Do Q ": wrap it in the shape clip
        xref = page.get_contents()[-1]
        polys = [apply(A, p) for p in outline_polys(d, bleed)]
        if polys:
            body = doc.xref_stream(xref)
            doc.update_stream(xref, b"q\n" + _clip_ops(polys, Hpt).encode() + body + b"\nQ\n")
    mode = getattr(sheet, "mark_mode", "auto")
    sheet.mark_colors = _mark_colors(page, sheet, mode, spacing_of)
    _draw_marks(page, sheet.marks, sheet.mark_colors)
    label = getattr(sheet, "label", None)
    if label:
        sheet.label_placed = _draw_label(page, sheet, label, spacing_of, getattr(sheet, "edge_mm", 6.0),
                                         white=(mode == "white"))
    return doc


def render_art(sheet, spacing_of, sources, max_px=2200):
    """Picture (BGR numpy) of the print sheet, longest side about max_px pixels.
    Returns (image, pixels per mm)."""
    doc = build_print_doc(sheet, spacing_of, sources)
    ppm = max_px / max(sheet.W, sheet.H)
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(ppm / MM_TO_PT, ppm / MM_TO_PT), alpha=False)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if pix.n == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    doc.close()
    return img, pix.width / sheet.W


def write_sheet(sheet, i, n, out_dir, job, spacing_of, sources, make_preview=False, ver=1):
    """sources: dict id(design) -> open pymupdf doc of its print art (reused across sheets)."""
    pname, cname, vname = file_names(job, i, n, sheet, ver)
    sheet.label = sheet_base(job, i, n, sheet, ver)
    Wpt, Hpt = sheet.W * MM_TO_PT, sheet.H * MM_TO_PT

    # ---------------- cut
    doc = pymupdf.open()
    page = doc.new_page(width=Wpt, height=Hpt)
    for d, q, A in sheet.placements:
        for poly, col in zip(d.cut, d.colors):
            pts = apply(A, poly) * MM_TO_PT
            closed = math.dist(poly[0], poly[-1]) <= 0.2
            sh = page.new_shape()
            sh.draw_polyline([pymupdf.Point(x, y) for x, y in pts])
            sh.finish(color=_rgb(col), fill=None, width=CUT_LINE_WIDTH_PT, closePath=closed)
            sh.commit()
    if getattr(sheet, "trim", False):
        # trim line around the whole sheet, in its own colour and last in the file, so it can
        # go on its own layer and be cut after the pieces
        sh = page.new_shape()
        sh.draw_rect(pymupdf.Rect(0, 0, Wpt, Hpt))
        sh.finish(color=TRIM_RGB, fill=None, width=CUT_LINE_WIDTH_PT, closePath=True)
        sh.commit()
    _draw_marks(page, sheet.marks)
    doc.set_metadata({"title": f"{job} sheet {i} of {n} - cut", "author": "", "creator": "SheetMaker",
                      "producer": "SheetMaker"})
    cut_bytes = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    with open(os.path.join(out_dir, cname), "wb") as f:
        f.write(cut_bytes)

    # ---------------- print (none for a sheet of blanks only: nothing to print, cut it directly)
    # Two-print sheets: Print 2 (top) carries the dots and name; Print 1 (printed first,
    # underneath) is the artwork only. Same page size and positions, so they line up when joined.
    written = []
    for label, name in print_names(job, i, n, sheet, ver):
        if label == "PRINT 1":
            doc = build_print_doc(under_sheet(sheet), spacing_of, sources)
            title = "print 1 (first)"
        else:
            doc = build_print_doc(sheet, spacing_of, sources)
            title = "print 2 (on top)" if label == "PRINT 2" else "print"
        doc.set_metadata({"title": f"{job} sheet {i} of {n} - {title}", "author": "", "creator": "SheetMaker",
                          "producer": "SheetMaker"})
        doc.save(os.path.join(out_dir, name), garbage=3, deflate=True)
        doc.close()
        written.append(name)
    written = tuple(written) + (cname,)
    # ---------------- preview (optional): print art with the cut lines on top
    if not make_preview:
        return written
    art, ppm = render_art(sheet, spacing_of, sources, max_px=3000)
    pdir = os.path.join(out_dir, PREVIEW_DIR)
    os.makedirs(pdir, exist_ok=True)
    # cv2.imwrite can't handle non-ASCII paths on Windows; encode in memory and write ourselves
    ok, buf = cv2.imencode(".png", render_preview(sheet, ppm, art=art))
    with open(os.path.join(pdir, vname), "wb") as f:
        f.write(buf.tobytes())
    return written + (os.path.join(PREVIEW_DIR, vname),)


# show_pdf_page turns the art this way round for one of our quarter turns (checked by test)
ROTATE_SIGN = 1


PALETTE = [(200, 0, 200), (0, 120, 220), (0, 160, 80), (220, 120, 0), (180, 0, 0), (90, 60, 200)]


CUT_PREVIEW_BGR = (255, 0, 200)      # cut lines in the preview: bright purple-blue, shows on most art


def mark_color(sheet, i):
    cols = getattr(sheet, "mark_colors", None)
    if cols and i < len(cols):
        return cols[i]
    return "white" if getattr(sheet, "mark_mode", "auto") == "white" else "black"


def draw_mark(img, cx, cy, r, color):
    """A dot on a preview picture (pixel units). White dots get a thin grey ring so
    they show on white too."""
    c = (int(cx * 8), int(cy * 8))
    rr = max(1, int(r * 8))
    if color == "white":
        cv2.circle(img, c, rr, (255, 255, 255), -1, lineType=cv2.LINE_AA, shift=3)
        cv2.circle(img, c, rr, (128, 128, 128), 1, lineType=cv2.LINE_AA, shift=3)
    else:
        cv2.circle(img, c, rr, (0, 0, 0), -1, lineType=cv2.LINE_AA, shift=3)


def render_preview(sheet, px_per_mm=2.0, art=None, show_cut=True):
    """Sheet picture: the print art (if given, already at px_per_mm) or white,
    cut lines on top, marks, sheet border."""
    s = px_per_mm
    h, w = max(1, int(round(sheet.H * s))), max(1, int(round(sheet.W * s)))
    if art is not None:
        img = art.copy() if art.shape[:2] == (h, w) else cv2.resize(art, (w, h), interpolation=cv2.INTER_AREA)
    else:
        img = np.full((h, w, 3), 255, np.uint8)
    if show_cut:
        for d, q, A in sheet.placements:
            for p in d.cut:
                cv2.polylines(img, [np.round(apply(A, p) * s * 8).astype(np.int32)], False, CUT_PREVIEW_BGR, 1,
                              lineType=cv2.LINE_AA, shift=3)
    for i, (mx, my) in enumerate(sheet.marks):
        draw_mark(img, mx * s, my * s, MARK_DIAMETER_MM / 2 * s, mark_color(sheet, i))
    if getattr(sheet, "trim", False):
        cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), TRIM_PREVIEW_BGR, 2)
    else:
        cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), (0, 0, 0), 1)
    return img
