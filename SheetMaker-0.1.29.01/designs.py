"""
designs.py - Load a design (PDF or PDF-compatible .ai) and split it into
  * the PRINT art  (everything except the cut line), kept as a vector PDF page
  * the CUT line   (stroked paths), as polylines in mm

How the cut line is found, in this order:
  1. a separate cut PDF given by the user (print + cut pair)
  2. a layer (optional content group) whose name contains one of the cut words
  3. a spot colour (Separation / DeviceN) whose name contains one of the cut words,
     e.g. CutContour, Cut Contour, Thru-cut
  4. strokes of the cut-line colour (Settings, default magenta #ff00ff)

Coordinates: mm from the page's top-left corner, X right, Y down.
"""

import io
import math
import os
import re

import numpy as np
import pikepdf
import pymupdf

PT_TO_MM = 25.4 / 72.0
MM_TO_PT = 72.0 / 25.4
FLATTEN_TOL_MM = 0.01

DEFAULT_CUT_WORDS = ("cut",)
DEFAULT_CUT_RGB = (1.0, 0.0, 1.0)      # magenta stroke = cut line


class DesignError(Exception):
    pass


class NoCutLine(DesignError):
    """The file opened fine, but no cut line was recognised: let the user pick one."""


def describe_style(key):
    """('RGB 1 0 1', 0.25, 'Cut') -> 'RGB 1 0 1, 0.25 pt, layer Cut'."""
    cdesc, width, layer = key
    out = f"{cdesc}, {width:g} pt"
    if layer:
        out += f", layer {layer}"
    return out


# ------------------------------------------------------------------ path flattening


def _flatten_cubic(p0, p1, p2, p3, tol, out, depth=0):
    ux = 3 * p1[0] - 2 * p0[0] - p3[0]
    uy = 3 * p1[1] - 2 * p0[1] - p3[1]
    vx = 3 * p2[0] - p0[0] - 2 * p3[0]
    vy = 3 * p2[1] - p0[1] - 2 * p3[1]
    if max(ux * ux, vx * vx) + max(uy * uy, vy * vy) <= 16 * tol * tol or depth > 16:
        out.append(p3)
        return
    m01 = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
    m12 = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
    m23 = ((p2[0] + p3[0]) / 2, (p2[1] + p3[1]) / 2)
    a = ((m01[0] + m12[0]) / 2, (m01[1] + m12[1]) / 2)
    b = ((m12[0] + m23[0]) / 2, (m12[1] + m23[1]) / 2)
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    _flatten_cubic(p0, m01, a, mid, tol, out, depth + 1)
    _flatten_cubic(mid, b, m23, p3, tol, out, depth + 1)


def drawing_to_polylines(d):
    """One PyMuPDF drawing -> list of polylines in mm."""
    polys, cur = [], None

    def P(pt):
        return (pt.x * PT_TO_MM, pt.y * PT_TO_MM)

    def start(pt):
        nonlocal cur
        if cur is None or math.dist(cur[-1], pt) > 1e-6:
            if cur and len(cur) > 1:
                polys.append(cur)
            cur = [pt]

    for it in d["items"]:
        kind = it[0]
        if kind == "l":
            a, b = P(it[1]), P(it[2])
            start(a)
            cur.append(b)
        elif kind == "c":
            a, c1, c2, b = P(it[1]), P(it[2]), P(it[3]), P(it[4])
            start(a)
            _flatten_cubic(a, c1, c2, b, FLATTEN_TOL_MM, cur)
        elif kind == "re":
            r = it[1]
            if cur and len(cur) > 1:
                polys.append(cur)
            pts = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1), (r.x0, r.y0)]
            polys.append([(x * PT_TO_MM, y * PT_TO_MM) for x, y in pts])
            cur = None
        elif kind == "qu":
            q = it[1]
            if cur and len(cur) > 1:
                polys.append(cur)
            polys.append([P(p) for p in (q.ul, q.ur, q.lr, q.ll, q.ul)])
            cur = None
    if cur and len(cur) > 1:
        polys.append(cur)
    if d.get("closePath"):
        for p in polys:
            if math.dist(p[0], p[-1]) > 1e-6:
                p.append(p[0])
    return polys


def _hexcol(col):
    col = col or (0, 0, 0)
    return "#%02x%02x%02x" % tuple(int(round(max(0, min(1, c)) * 255)) for c in col[:3])


# ------------------------------------------------------------------ content-stream splitting

_STROKE = {"S", "s"}
_FILLSTROKE = {"B", "B*", "b", "b*"}
_FILL = {"f", "F", "f*"}
_PAINT = _STROKE | _FILLSTROKE | _FILL | {"n"}


def _name(o):
    try:
        return str(o)[1:] if str(o).startswith("/") else str(o)
    except Exception:
        return ""


def _mat_mul(m, n):
    """PDF matrices [a b c d e f]: m then n."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def _fmt(v):
    return ("%.3f" % float(v)).rstrip("0").rstrip(".")


class _Splitter:
    """Walks a page's content. Every stroked path gets a style key
    (colour, line width as printed, layer). Modes:
      'survey' - list the stroke styles with their geometry (for picking)
      'print'  - remove the cut strokes, keep everything else
      'cut'    - keep only the cut strokes
    A stroke is a cut stroke if its style is in `pick`; with no pick, the automatic
    rules apply: a layer / spot colour whose name contains a cut word, or the cut colour."""

    def __init__(self, pdf, words, cut_rgb=None, rgb_tol=0.12, pick=None, page_box=None):
        self.pdf = pdf
        self.rx = re.compile("|".join(re.escape(w) for w in words), re.I) if words else None
        self.cut_rgb = cut_rgb
        self.rgb_tol = rgb_tol
        self.pick = set(pick) if pick else None
        self.found = set()          # how the cut line was recognised (for the UI)
        self.n_cut_paths = 0
        self.done_forms = set()
        self.styles = {}            # survey: key -> {count, rgb, label, polys, auto}
        self.page_box = page_box    # (x0, y0, x1, y1) in pt, to turn PDF space into top-left mm

    # -------------------------------------------------- tests
    def _word(self, s):
        return bool(self.rx and s and self.rx.search(s))

    def _ocg_name(self, obj):
        try:
            t = _name(obj.get("/Type", ""))
            if t == "OCG":
                return str(obj.get("/Name", ""))
            if t == "OCMD":
                ocgs = obj.get("/OCGs")
                if ocgs is None:
                    return ""
                if isinstance(ocgs, pikepdf.Array):
                    return ", ".join(self._ocg_name(o) for o in ocgs)
                return self._ocg_name(ocgs)
        except Exception:
            pass
        return ""

    def _colour_space(self, cs, res):
        """-> (kind, name) e.g. ('RGB', ''), ('Spot', 'CutContour')."""
        n = _name(cs)
        if n in ("DeviceRGB", "CalRGB"):
            return ("RGB", "")
        if n in ("DeviceCMYK",):
            return ("CMYK", "")
        if n in ("DeviceGray", "CalGray"):
            return ("Gray", "")
        if n == "Pattern":
            return ("Pattern", "")
        try:
            arr = res.get("/ColorSpace", {}).get("/" + n)
        except Exception:
            arr = None
        if isinstance(arr, pikepdf.Array) and len(arr) >= 2:
            kind = _name(arr[0])
            if kind == "Separation":
                return ("Spot", _name(arr[1]))
            if kind == "DeviceN":
                return ("Spot", ", ".join(_name(x) for x in arr[1]))
            if kind == "ICCBased":
                try:
                    nc = int(arr[1].get("/N", 3))
                except Exception:
                    nc = 3
                return ({1: "Gray", 3: "RGB", 4: "CMYK"}.get(nc, "RGB"), "")
            if kind in ("CalRGB", "Lab"):
                return ("RGB", "")
            if kind == "Indexed":
                return ("Indexed", "")
        return ("RGB", "")

    @staticmethod
    def _to_rgb(kind, vals):
        try:
            v = [float(x) for x in vals]
        except Exception:
            return None
        if kind == "Gray" and len(v) >= 1:
            return (v[0],) * 3
        if kind == "RGB" and len(v) >= 3:
            return tuple(v[:3])
        if kind == "CMYK" and len(v) >= 4:
            c, m, y, k = v[:4]
            return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
        return None

    def _auto_cut(self, st, layer_name):
        if layer_name and self._word(layer_name):
            self.found.add(f"layer '{layer_name}'")
            return True
        kind, name = st["cs"]
        if kind == "Spot" and self._word(name):
            self.found.add(f"spot colour '{name}'")
            return True
        rgb = self._to_rgb(kind, st["col"])
        if self.cut_rgb and rgb is not None and \
                max(abs(a - b) for a, b in zip(rgb, self.cut_rgb)) <= self.rgb_tol:
            self.found.add("stroke colour %s" % _hexcol(self.cut_rgb))
            return True
        return False

    def _style(self, st, layer_name):
        kind, name = st["cs"]
        m = st["ctm"]
        scale = math.sqrt(abs(m[0] * m[3] - m[1] * m[2])) or 1.0
        width = round(st["w"] * scale, 2)
        col = " ".join(_fmt(v) for v in st["col"])
        cdesc = f"{kind} {name} {col}".replace("  ", " ").strip()
        return (cdesc, width, layer_name or "")

    # -------------------------------------------------- walking
    def run(self, page_or_form, res, mode, in_layer="", ctm=(1, 0, 0, 1, 0, 0), depth=0):
        ops = pikepdf.parse_content_stream(page_or_form)
        out = []
        gs = [{"cs": ("Gray", ""), "col": (0.0,), "w": 1.0, "ctm": tuple(ctm)}]
        mc = [in_layer]                    # marked-content stack: layer names
        text_depth = 0
        props = res.get("/Properties", {}) if res is not None else {}
        xobjs = res.get("/XObject", {}) if res is not None else {}
        path, cur = [], None               # survey: subpaths in PDF user space (after CTM)
        survey = mode == "survey"

        def layer():
            for n in reversed(mc):
                if n:
                    return n
            return ""

        def P(x, y):
            m = gs[-1]["ctm"]
            x, y = float(x), float(y)
            return (x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5])

        for operands, op in ops:
            o = str(op)
            st = gs[-1]
            if o == "q":
                gs.append(dict(st))
            elif o == "Q":
                if len(gs) > 1:
                    gs.pop()
            elif o == "cm":
                st["ctm"] = _mat_mul(tuple(float(x) for x in operands), st["ctm"])
            elif o == "w":
                st["w"] = float(operands[0])
            elif o == "CS":
                st["cs"] = self._colour_space(operands[0], res)
                st["col"] = (0.0,) if st["cs"][0] != "Spot" else (1.0,)
            elif o in ("SC", "SCN"):
                nums = [x for x in operands if not isinstance(x, pikepdf.Name)]
                st["col"] = tuple(float(x) for x in nums)
            elif o == "RG":
                st["cs"], st["col"] = ("RGB", ""), tuple(float(x) for x in operands)
            elif o == "K":
                st["cs"], st["col"] = ("CMYK", ""), tuple(float(x) for x in operands)
            elif o == "G":
                st["cs"], st["col"] = ("Gray", ""), tuple(float(x) for x in operands)
            elif o == "BDC":
                name = ""
                if len(operands) >= 2 and _name(operands[0]) == "OC":
                    p = operands[1]
                    if isinstance(p, pikepdf.Name):
                        p = props.get(p) if props is not None else None
                    if p is not None:
                        name = self._ocg_name(p)
                mc.append(name)
            elif o == "BMC":
                mc.append("")
            elif o == "EMC":
                if len(mc) > 1:
                    mc.pop()

            # path geometry (only needed for the survey)
            if survey and o in ("m", "l", "c", "v", "y", "h", "re"):
                if o == "m":
                    cur = [P(*operands)]
                    path.append(cur)
                elif o == "l" and cur is not None:
                    cur.append(P(*operands))
                elif o in ("c", "v", "y") and cur is not None:
                    pts = [P(operands[i], operands[i + 1]) for i in range(0, len(operands), 2)]
                    p0 = cur[-1]
                    if o == "c":
                        c1, c2, p3 = pts
                    elif o == "v":
                        c1, c2, p3 = p0, pts[0], pts[1]
                    else:
                        c1, c2, p3 = pts[0], pts[1], pts[1]
                    _flatten_cubic(p0, c1, c2, p3, 0.3, cur)
                elif o == "h" and cur:
                    cur.append(cur[0])
                elif o == "re":
                    x, y, w, h = (float(v) for v in operands)
                    cur = [P(x, y), P(x + w, y), P(x + w, y + h), P(x, y + h), P(x, y)]
                    path.append(cur)

            # ---------------- decide what to emit
            if o == "BT":
                text_depth += 1
            if text_depth:
                if o == "ET":
                    text_depth -= 1
                if mode == "print" and not self._layer_is_cut(layer()):
                    out.append((operands, op))
                continue
            if o in _PAINT:
                stroked = o in _STROKE or o in _FILLSTROKE
                is_cut = False
                if stroked:
                    key = self._style(st, layer())
                    auto = self._auto_cut(st, layer())
                    is_cut = (key in self.pick) if self.pick is not None else auto
                    if survey:
                        e = self.styles.get(key)
                        if e is None:
                            kind, name = st["cs"]
                            e = self.styles[key] = {"count": 0, "rgb": self._to_rgb(kind, st["col"]),
                                                    "kind": kind, "spot": name, "auto": auto, "polys": []}
                        e["count"] += 1
                        if o in ("s", "b", "b*"):
                            for sp in path:
                                if sp and sp[-1] != sp[0]:
                                    sp.append(sp[0])
                        e["polys"] += [self._to_mm(sp) for sp in path if len(sp) > 1]
                path, cur = [], None
                whole_layer_cut = self.pick is None and self._layer_is_cut(layer())
                if mode == "print":
                    if whole_layer_cut:
                        o2 = "n"
                    elif o in _STROKE and is_cut:
                        o2 = "n"
                    elif o in _FILLSTROKE and is_cut:
                        o2 = {"B": "f", "B*": "f*", "b": "f", "b*": "f*"}[o]
                        if o in ("b", "b*"):
                            out.append(([], pikepdf.Operator("h")))
                    else:
                        o2 = o
                elif mode == "cut":
                    if is_cut and stroked:
                        self.n_cut_paths += 1
                        o2 = "s" if o in ("s", "b", "b*") else "S"
                    else:
                        o2 = "n"
                else:
                    o2 = o
                out.append(([], pikepdf.Operator(o2)))
                continue
            if o == "Do":
                xo = xobjs.get(operands[0]) if xobjs is not None else None
                if xo is None:
                    continue
                sub = _name(xo.get("/Subtype", ""))
                xo_layer = layer()
                oc = xo.get("/OC")
                if oc is not None:
                    xo_layer = self._ocg_name(oc) or xo_layer
                if sub == "Form":
                    fres = xo.get("/Resources", res)
                    fm = tuple(float(x) for x in xo.get("/Matrix", [1, 0, 0, 1, 0, 0]))
                    fctm = _mat_mul(fm, st["ctm"])
                    if survey:
                        if depth < 12:
                            self.run(xo, fres, mode, xo_layer, fctm, depth + 1)
                    else:
                        key = (xo.objgen, mode)
                        if key not in self.done_forms:
                            self.done_forms.add(key)
                            new = self.run(xo, fres, mode, xo_layer, fctm, depth + 1)
                            xo.write(new)
                    out.append((operands, op))
                else:   # image
                    if mode == "print" and not self._layer_is_cut(xo_layer):
                        out.append((operands, op))
                continue
            if o in ("sh", "INLINE IMAGE", "BI", "ID", "EI"):
                if mode == "print" and not self._layer_is_cut(layer()):
                    out.append((operands, op))
                continue
            out.append((operands, op))
        return pikepdf.unparse_content_stream(out)

    def _layer_is_cut(self, name):
        """With a manual pick only picked strokes are cut; layers are not dropped wholesale."""
        if self.pick is not None:
            return False
        return bool(name) and self._word(name)

    def _to_mm(self, pts):
        x0, y0, x1, y1 = self.page_box or (0, 0, 0, 0)
        return np.array([((x - x0) * PT_TO_MM, (y1 - y) * PT_TO_MM) for x, y in pts], float)


def _page_box(page):
    box = page.obj.get("/CropBox") or page.obj.get("/MediaBox")
    v = [float(x) for x in box]
    return (min(v[0], v[2]), min(v[1], v[3]), max(v[0], v[2]), max(v[1], v[3]))


def _split(path, mode, words, cut_rgb, pick=None):
    pdf = pikepdf.open(path)
    if len(pdf.pages) < 1:
        raise DesignError("The file has no pages.")
    while len(pdf.pages) > 1:          # keep only page 1
        del pdf.pages[-1]
    page = pdf.pages[0]
    sp = _Splitter(pdf, words, cut_rgb, pick=pick, page_box=_page_box(page))
    page.contents_coalesce()
    res = page.obj.get("/Resources", pikepdf.Dictionary())
    new = sp.run(page.obj, res, mode)
    if mode == "survey":
        return None, sp
    page.obj.Contents = pdf.make_stream(new)
    if "/Annots" in page.obj:
        del page.obj["/Annots"]
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue(), sp


def survey_strokes(path, words=DEFAULT_CUT_WORDS, cut_rgb=DEFAULT_CUT_RGB):
    """Every kind of stroked line in the file: list of dicts
    {key, count, rgb, kind, spot, width, layer, auto, polys(mm, top-left)} - most likely cut line first."""
    _, sp = _split(path, "survey", words, cut_rgb)
    out = []
    for key, e in sp.styles.items():
        cdesc, width, layer = key
        out.append(dict(e, key=key, width=width, layer=layer))
    out.sort(key=lambda e: (not e["auto"], -e["count"]))
    return out


def page_size_mm(path):
    d = pymupdf.open(path)
    r = d[0].rect if not d[0].rotation else d[0].mediabox
    d.close()
    return r.width * PT_TO_MM, r.height * PT_TO_MM


def _stroked_polylines(doc_bytes, skip_dots=False):
    doc = pymupdf.open("pdf", doc_bytes)
    page = doc[0]
    rot = page.rotation
    if rot:
        page.set_rotation(0)
    polys, colors = [], []
    for d in page.get_drawings():
        if d["type"] not in ("s", "fs"):
            continue
        r = d["rect"]
        w, h = r.width * PT_TO_MM, r.height * PT_TO_MM
        if skip_dots and d["type"] == "fs" and 1 <= w <= 20 and abs(w - h) <= 0.05 * max(w, h):
            continue
        col = _hexcol(d.get("color"))
        for p in drawing_to_polylines(d):
            if len(p) >= 2:
                polys.append(np.array(p, float))
                colors.append(col)
    size = (page.rect.width * PT_TO_MM, page.rect.height * PT_TO_MM)
    doc.close()
    return polys, colors, size


def _norot(doc_bytes):
    """Page rotation set to 0 so art and cut use the same frame."""
    doc = pymupdf.open("pdf", doc_bytes)
    if doc[0].rotation:
        doc[0].set_rotation(0)
    out = doc.tobytes()
    doc.close()
    return out


# ------------------------------------------------------------------ Design


class Design:
    def __init__(self, print_path, cut_path=None, qty=1, words=DEFAULT_CUT_WORDS,
                 cut_rgb=DEFAULT_CUT_RGB, bleed_mm=3.0, pick=None, allow_no_cut=False, blank=False):
        self.print_path = print_path
        self.cut_path = cut_path
        self.qty = int(qty)
        self.name = os.path.splitext(os.path.basename(print_path))[0]
        self.notes = []
        self.bleed_mm = bleed_mm
        self.allow_rotate = True
        self.pick = set(pick) if pick else None     # manually picked cut-line styles
        self.needs_cut = False                      # print loaded, cut line still to be picked / added
        self.spacing = None                         # None = job default
        self.blank = bool(blank)                    # cut only: nothing printed for this design
        try:
            if blank:
                # a cut-only file on its own: every line in it is cut, nothing is printed
                self.print_pdf = None
                self.cut, self.colors, _ = _stroked_polylines(_norot(pymupdf.open(print_path).tobytes()),
                                                              skip_dots=True)
                self.source = "blank (cut only)"
            elif cut_path:
                self.print_pdf = _norot(pymupdf.open(print_path).tobytes())
                self.cut, self.colors, csize = _stroked_polylines(_norot(pymupdf.open(cut_path).tobytes()),
                                                                  skip_dots=True)
                self.source = "separate cut file: " + os.path.basename(cut_path)
                psize = self._page_size(self.print_pdf)
                self.cut = _center_cut(self.cut, csize, psize, self.notes)
            else:
                cut_bytes, sp = _split(print_path, "cut", words, cut_rgb, self.pick)
                self.cut, self.colors, _ = _stroked_polylines(_norot(cut_bytes))
                if not self.cut:
                    if not allow_no_cut:
                        raise NoCutLine(
                            f"No cut line found in {os.path.basename(print_path)}. Use 'Pick cut line…' to choose "
                            "it, or add a separate cut file.")
                    # keep the print as it is; the cut line comes later
                    self.print_pdf = _norot(pymupdf.open(print_path).tobytes())
                    self.needs_cut = True
                    self.source = "no cut line yet"
                    w, h = self._page_size(self.print_pdf)
                    self.cut_box = (0.0, 0.0, w, h)
                    self.size_mm = (w, h)
                    return
                print_bytes, _ = _split(print_path, "print", words, cut_rgb, self.pick)
                self.print_pdf = _norot(print_bytes)
                if self.pick:
                    self.source = "picked: " + "; ".join(describe_style(k) for k in sorted(self.pick))
                else:
                    self.source = " + ".join(sorted(sp.found)) or "cut line"
        except DesignError:
            raise
        except pikepdf.PasswordError:
            raise DesignError(f"{os.path.basename(print_path)} is password protected.")
        except Exception as e:
            raise DesignError(f"Could not read {os.path.basename(print_path)}: {e}")
        if not self.cut:
            raise DesignError("The cut file has no cut lines (stroked paths).")
        pts = np.vstack(self.cut)
        self.cut_box = (pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max())
        x0, y0, x1, y1 = self.cut_box
        self.size_mm = (x1 - x0, y1 - y0)
        open_paths = sum(1 for p in self.cut if math.dist(p[0], p[-1]) > 0.2)
        if open_paths:
            self.notes.append(f"{open_paths} cut path(s) are not closed.")

    @staticmethod
    def _page_size(b):
        d = pymupdf.open("pdf", b)
        r = d[0].rect
        d.close()
        return r.width * PT_TO_MM, r.height * PT_TO_MM

    def clip_box(self, bleed=None):
        """Area of the print art taken with each copy: the cut line plus bleed."""
        b = self.bleed_mm if bleed is None else bleed
        x0, y0, x1, y1 = self.cut_box
        return (x0 - b, y0 - b, x1 + b, y1 + b)


def is_cut_only(path):
    """True if a file holds only stroked lines of one colour (no fills, images or text):
    the cut half of a print + cut pair. Line art in several colours (e.g. a black outline
    plus a magenta cut line) is a normal one-file design."""
    try:
        doc = pymupdf.open(path)
        page = doc[0]
        if page.get_images() or page.get_text("text").strip():
            return False
        dr = page.get_drawings()
        if not dr:
            return False
        colours = set()
        for d in dr:
            if d["type"] == "s":
                colours.add(_hexcol(d.get("color")))
                continue
            r = d["rect"]
            w, h = r.width * PT_TO_MM, r.height * PT_TO_MM
            if 1 <= w <= 20 and abs(w - h) <= 0.05 * max(w, h):
                continue          # registration dot
            return False
        return len(colours) <= 1
    except Exception:
        return False


_PAIR_WORDS = re.compile(r"(cut[ _-]*(contour|line|lines|file|path)?|print|art|contour)", re.I)


def pair_key(path):
    n = os.path.splitext(os.path.basename(path))[0]
    n = _PAIR_WORDS.sub("", n)
    return re.sub(r"[^a-z0-9]", "", n.lower())


def pair_files(paths):
    """Split a list of picked files into [(print, cut_or_None)], unmatched cut files."""
    cuts = [p for p in paths if is_cut_only(p)]
    prints = [p for p in paths if p not in cuts]
    by_key = {}
    for c in cuts:
        by_key.setdefault(pair_key(c), []).append(c)
    out, used = [], set()
    def score(a, b):
        # length of the shared start of the two cleaned names
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        return n / max(1, max(len(a), len(b)))

    for p in prints:
        k = pair_key(p)
        c = next((c for c in by_key.get(k, []) if c not in used), None)
        if c is None:
            cands = [(score(k, pair_key(c)), c) for c in cuts if c not in used]
            cands = [x for x in cands if x[0] >= 0.75]
            if cands:
                c = max(cands)[1]
        if c is None and len(prints) == 1 and len(cuts) == 1:
            c = cuts[0]
        if c:
            used.add(c)
        out.append((p, c))
    return out, [c for c in cuts if c not in used]


# ------------------------------------------------------------------ multi-page files

def split_pages(paths, progress=None):
    """Files with several pages are broken out: each page becomes its own one-page PDF in a
    temp folder, so it loads like a separate file. Returns (paths, names) where names maps
    each page file to a display name such as 'charms p2'. One-page files pass through."""
    import hashlib
    import tempfile
    out, names = [], {}
    root = os.path.join(tempfile.gettempdir(), "SheetMaker_pages")
    for path in paths:
        try:
            n = len(pymupdf.open(path))
        except Exception:
            out.append(path)
            continue
        if n <= 1:
            out.append(path)
            continue
        st = os.stat(path)
        tag = hashlib.md5(f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime}".encode()).hexdigest()[:10]
        folder = os.path.join(root, tag)
        os.makedirs(folder, exist_ok=True)
        stem = os.path.splitext(os.path.basename(path))[0]
        src = None
        for i in range(n):
            if progress:
                progress(f"Splitting {os.path.basename(path)}: page {i + 1} of {n}…")
            dest = os.path.join(folder, f"{stem}_p{i + 1}.pdf")
            if not os.path.exists(dest):
                if src is None:
                    src = pikepdf.open(path)
                one = pikepdf.new()
                one.pages.append(src.pages[i])
                pg = one.pages[0].obj
                if "/PieceInfo" in pg:            # Illustrator's editing data: not printed, often huge
                    del pg["/PieceInfo"]
                one.save(dest + ".part")
                os.replace(dest + ".part", dest)
            out.append(dest)
            names[dest] = f"{stem} p{i + 1}"
    return out, names


# ------------------------------------------------------------------ two-print (layered) designs

MIRROR_NONE, MIRROR_LR, MIRROR_TB = "", "lr", "tb"
MIRROR_TEXT = {MIRROR_NONE: "", MIRROR_LR: "mirrored left-right", MIRROR_TB: "mirrored top-bottom"}


def mirror_pdf(doc_bytes, how):
    """The page mirrored in place (vector, nothing rasterised): 'lr' = left <-> right, 'tb' = top <-> bottom."""
    if how not in (MIRROR_LR, MIRROR_TB):
        return doc_bytes
    pdf = pikepdf.open(io.BytesIO(doc_bytes))
    pg = pdf.pages[0]
    box = pg.obj.get("/CropBox") or pg.obj.get("/MediaBox")
    x0, y0, x1, y1 = (float(v) for v in box)
    m = f"-1 0 0 1 {x0 + x1:.4f} 0 cm" if how == MIRROR_LR else f"1 0 0 -1 0 {y0 + y1:.4f} cm"
    pg.contents_add(pikepdf.Stream(pdf, f"q {m}\n".encode()), prepend=True)
    pg.contents_add(pikepdf.Stream(pdf, b"\nQ\n"))
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def mirror_polys(polys, size_mm, how):
    w, h = size_mm
    if how == MIRROR_LR:
        return [np.column_stack([w - p[:, 0], p[:, 1]]) for p in polys]
    if how == MIRROR_TB:
        return [np.column_stack([p[:, 0], h - p[:, 1]]) for p in polys]
    return [np.asarray(p, float) for p in polys]


def ink_coverage(path):
    """Share of the page that isn't white (a cut-line page has very little)."""
    try:
        doc = pymupdf.open(path)
        pix = doc[0].get_pixmap(dpi=20, colorspace=pymupdf.csGRAY, alpha=False)
        g = np.frombuffer(pix.samples, np.uint8)
        doc.close()
        return float((g < 200).mean())
    except Exception:
        return 1.0


class LayeredDesign(Design):
    """Pages of one artboard used together: a cut page, the top print (Print 2, carries the dots) and
    optionally a first print (Print 1) printed underneath it. Each page can be mirrored in
    its own place. With Print 1 the sheet gets two print files (_P1 first, _P2 on top)."""

    def __init__(self, name, cut, print2, print1=None):
        """cut / print2 / print1: (page pdf path, mirror) with mirror '', 'lr' or 'tb'."""
        self.name = name
        self.qty = 1
        self.notes = []
        self.bleed_mm = 3.0
        self.allow_rotate = True
        self.pick = None
        self.needs_cut = False
        self.spacing = None
        self.blank = False
        self.roles = {"cut": cut, "print2": print2, "print1": print1}
        try:
            cut_bytes = _norot(pymupdf.open(cut[0]).tobytes())
            p2 = _norot(pymupdf.open(print2[0]).tobytes())
            p1 = _norot(pymupdf.open(print1[0]).tobytes()) if print1 else None
        except Exception as e:
            raise DesignError(f"Could not read the pages: {e}")
        sizes = [self._page_size(b) for b in (p2, p1) if b is not None]
        if max(abs(s[0] - sizes[0][0]) + abs(s[1] - sizes[0][1]) for s in sizes) > 0.5:
            raise DesignError("The print pages are different sizes (" +
                              ", ".join(f"{w:.1f} x {h:.1f}" for w, h in sizes) +
                              " mm). Print 1 and Print 2 need pages of the same size, so they line up.")
        self.cut, self.colors, csize = _stroked_polylines(cut_bytes, skip_dots=True)
        if not self.cut:
            raise DesignError("The cut page has no cut lines (stroked paths).")
        self.cut = mirror_polys(self.cut, csize, cut[1])
        self.cut = _center_cut(self.cut, csize, sizes[0], self.notes)   # a smaller cut page: print has bleed
        self.print_pdf = mirror_pdf(p2, print2[1])               # top print: what the preview shows
        self.under_pdf = mirror_pdf(p1, print1[1]) if p1 is not None else None
        self.print_path, self.cut_path = print2[0], cut[0]
        pts = np.vstack(self.cut)
        self.cut_box = (pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max())
        x0, y0, x1, y1 = self.cut_box
        self.size_mm = (x1 - x0, y1 - y0)
        self.layered = p1 is not None
        bits = []
        for label, role in (("cut", cut), ("print 1", print1), ("print 2", print2)):
            if role:
                m = {MIRROR_LR: " ↔", MIRROR_TB: " ↕"}.get(role[1], "")
                bits.append(f"{label} {_page_tag(role[0])}{m}")
        self.source = ("2 prints: " if self.layered else "pages: ") + ", ".join(bits)


def _center_cut(polys, csize, psize, notes):
    """A cut page of another size than the print page is centred on it: usually the print artboard
    has bleed added around the cut artboard."""
    dx, dy = (psize[0] - csize[0]) / 2, (psize[1] - csize[1]) / 2
    if max(abs(dx), abs(dy)) * 2 <= 0.5:
        return polys
    notes.append(f"Cut page {csize[0]:.1f} x {csize[1]:.1f} mm, print page {psize[0]:.1f} x {psize[1]:.1f} mm: "
                 "the cut is centred on the print (bleed).")
    return [np.asarray(p, float) + (dx, dy) for p in polys]


def _page_tag(path):
    m = re.search(r"_p(\d+)\.pdf$", path, re.I)
    return f"p{m.group(1)}" if m else os.path.basename(path)


def is_layered(d):
    return bool(getattr(d, "layered", False))
