"""
preview.py - Sheet preview: print artwork with the cut lines on top.
Scroll to zoom (around the mouse), drag to move, double-click to fit the sheet.

Artwork is drawn by a background thread: the whole sheet once, then a sharp
picture of whatever part is on screen after zooming.
"""

import base64
import queue
import threading
import tkinter as tk

import cv2
import numpy as np
import pymupdf

import output as O

MM_TO_PT = 72.0 / 25.4


# preview background colours (BGR): the material the sheet is printed on. Preview only.
BACKGROUNDS = {"White": (255, 255, 255), "Black": (0, 0, 0), "Gray": (128, 128, 128), "Red": (30, 30, 200)}


class ArtRenderer:
    """One background thread that owns all the PDF work for the preview."""

    def __init__(self, on_done):
        self.on_done = on_done            # called (from the thread) with (job_id, token, image, rect_mm, ppm)
        self.q = queue.Queue()
        self.docs = {}                    # sheet key -> print doc
        self.sources = {}
        self.spacing_of = lambda d: 5.0
        threading.Thread(target=self._loop, daemon=True).start()

    def reset(self, spacing_of):
        self.q.put(("reset", spacing_of))

    def request(self, job_id, sheet, rect_mm, ppm, token):
        # newest request wins: drop older pending ones for the same job
        self.q.put(("render", job_id, sheet, rect_mm, ppm, token))

    def _loop(self):
        while True:
            item = self.q.get()
            # skip to the newest render request
            pending = [item]
            while True:
                try:
                    pending.append(self.q.get_nowait())
                except queue.Empty:
                    break
            for it in pending:
                if it[0] == "reset":
                    for d in self.docs.values():
                        d.close()
                    for d in self.sources.values():
                        d.close()
                    self.docs, self.sources = {}, {}
                    self.spacing_of = it[1]
            renders = [it for it in pending if it[0] == "render"]
            if not renders:
                continue
            latest = {}
            for it in renders:
                latest[it[1]] = it           # per job id (whole-sheet vs zoomed view)
            for it in latest.values():
                _, job_id, sheet, rect, ppm, token = it
                try:
                    x0, y0, x1, y1 = rect
                    clip = pymupdf.Rect(x0 * MM_TO_PT, y0 * MM_TO_PT, x1 * MM_TO_PT, y1 * MM_TO_PT)
                    m = ppm / MM_TO_PT
                    imgs = []
                    layers = [("top", sheet)]
                    if O.is_layered_sheet(sheet):
                        layers.append(("under", None))
                    for which, sh in layers:
                        key = (which, id(sheet))
                        doc = self.docs.get(key)
                        if doc is None:
                            sh = sh if sh is not None else O.under_sheet(sheet)
                            doc = self.docs[key] = O.build_print_doc(sh, self.spacing_of, self.sources)
                        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(m, m), clip=clip, alpha=True)
                        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
                        imgs.append(cv2.cvtColor(img, cv2.COLOR_RGBA2BGRA if pix.n == 4 else cv2.COLOR_GRAY2BGRA))
                    top, under = imgs[0], (imgs[1] if len(imgs) > 1 else None)
                    self.on_done(job_id, token, (top, under), rect, pix.width / max(1e-9, (x1 - x0)))
                except Exception:
                    import traceback
                    traceback.print_exc()


class SheetView(tk.Frame):
    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, background="#d9d9d9", highlightthickness=0, cursor="fleur")
        self.canvas.pack(fill="both", expand=True)
        self.show_art = tk.BooleanVar(value=True)
        self.show_cut = tk.BooleanVar(value=True)
        self.background = tk.StringVar(value="White")      # preview only: colour of the material
        # two-print sheets: each print on / off, and Print 2 see-through to check it lines up with Print 1
        self.show_p1 = tk.BooleanVar(value=True)
        self.show_p2 = tk.BooleanVar(value=True)
        self.p2_half = tk.BooleanVar(value=False)
        self.sheet = None
        self.full = None          # (image, ppm) of the whole sheet
        self.sharp = None         # (image, rect_mm, ppm, token) of the zoomed view
        self.scale = 1.0          # screen px per mm
        self.off = (0.0, 0.0)     # screen position of the sheet's top-left
        self.token = 0
        self._img = None
        self._drag = None
        self._after = None
        self.inbox = queue.Queue()
        self.renderer = ArtRenderer(lambda *a: self.inbox.put(a))
        c = self.canvas
        c.bind("<Configure>", lambda e: self._fit() if self._fitted else self.redraw())
        c.bind("<MouseWheel>", self._wheel)                    # Windows
        c.bind("<Button-4>", lambda e: self._zoom_at(e.x, e.y, 1.25))
        c.bind("<Button-5>", lambda e: self._zoom_at(e.x, e.y, 0.8))
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._move)
        c.bind("<Double-1>", lambda e: self._fit())
        self._fitted = True
        self.after(60, self._poll)

    # ------------------------------------------------ public
    def set_spacing(self, spacing_of):
        self.renderer.reset(spacing_of)

    def show(self, sheet):
        self.sheet = sheet
        self.full = None
        self.sharp = None
        self.token += 1
        self._fit()
        if sheet is not None:
            ppm = min(4.0, 2600 / max(sheet.W, sheet.H))
            self.renderer.request("full", sheet, (0, 0, sheet.W, sheet.H), ppm, self.token)

    def clear(self):
        self.show(None)

    # ------------------------------------------------ view
    def _fit(self):
        self._fitted = True
        if self.sheet is None:
            self.redraw()
            return
        cw, ch = max(50, self.canvas.winfo_width()), max(50, self.canvas.winfo_height())
        self.scale = min((cw - 20) / self.sheet.W, (ch - 20) / self.sheet.H)
        self.off = ((cw - self.sheet.W * self.scale) / 2, (ch - self.sheet.H * self.scale) / 2)
        self.redraw()

    def _wheel(self, e):
        self._zoom_at(e.x, e.y, 1.25 if e.delta > 0 else 0.8)

    def _zoom_at(self, x, y, f):
        if self.sheet is None:
            return
        cw = max(50, self.canvas.winfo_width())
        fit = min((cw - 20) / self.sheet.W, (max(50, self.canvas.winfo_height()) - 20) / self.sheet.H)
        new = min(max(self.scale * f, fit * 0.5), 40.0)
        f = new / self.scale
        self.off = (x - (x - self.off[0]) * f, y - (y - self.off[1]) * f)
        self.scale = new
        self._fitted = False
        self.redraw()

    def _press(self, e):
        self._drag = (e.x, e.y, self.off)

    def _move(self, e):
        if not self._drag:
            return
        x0, y0, (ox, oy) = self._drag
        self.off = (ox + e.x - x0, oy + e.y - y0)
        self._fitted = False
        self.redraw()

    def _visible_mm(self):
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        x0 = max(0.0, -self.off[0] / self.scale)
        y0 = max(0.0, -self.off[1] / self.scale)
        x1 = min(self.sheet.W, (cw - self.off[0]) / self.scale)
        y1 = min(self.sheet.H, (ch - self.off[1]) / self.scale)
        return x0, y0, x1, y1

    def redraw(self):
        c = self.canvas
        c.delete("all")
        if self.sheet is None:
            return
        sh = self.sheet
        x0, y0, x1, y1 = self._visible_mm()
        if x1 <= x0 or y1 <= y0:
            return
        s = self.scale
        w, h = max(1, int(round((x1 - x0) * s))), max(1, int(round((y1 - y0) * s)))
        bg = np.array(BACKGROUNDS.get(self.background.get(), BACKGROUNDS["White"]), np.uint8)
        img = np.empty((h, w, 3), np.uint8)
        img[:] = bg
        if self.show_art.get():
            src = None
            if self.sharp and self.sharp[3] == self.token:
                simg, (sx0, sy0, sx1, sy1), sppm, _ = self.sharp
                if sx0 <= x0 + 1e-6 and sy0 <= y0 + 1e-6 and sx1 >= x1 - 1e-6 and sy1 >= y1 - 1e-6 \
                        and sppm >= s * 0.9:
                    src = (simg, sx0, sy0, sppm)
            if src is None and self.full:
                src = (self.full[0], 0.0, 0.0, self.full[1])
            if src is not None:
                (top, under), ox, oy, ppm = src
                cx0, cy0 = int((x0 - ox) * ppm), int((y0 - oy) * ppm)
                cx1, cy1 = int(np.ceil((x1 - ox) * ppm)), int(np.ceil((y1 - oy) * ppm))
                layers = []
                if under is not None and self.show_p1.get():
                    layers.append((under, 1.0))                       # Print 1, printed first
                if under is None or self.show_p2.get():
                    layers.append((top, 0.5 if under is not None and self.p2_half.get() else 1.0))
                for simg, opacity in layers:
                    crop = simg[max(0, cy0):cy1, max(0, cx0):cx1]
                    if not crop.size:
                        continue
                    art = cv2.resize(crop, (w, h), interpolation=cv2.INTER_AREA if ppm > s else cv2.INTER_LINEAR)
                    a = art[:, :, 3:4].astype(np.float32) / 255.0 * opacity
                    img = (art[:, :, :3] * a + img * (1 - a)).astype(np.uint8)
        # cut lines and marks, drawn at screen resolution
        if self.show_cut.get():
            lines = img.copy()
            for d, q, A in sh.placements:
                for p in d.cut:
                    pts = O.apply(A, p)
                    pts = pts * s - [x0 * s, y0 * s]
                    cv2.polylines(lines, [np.round(pts * 8).astype(np.int32)], False, O.CUT_PREVIEW_BGR,
                                  2 if s > 6 else 1, lineType=cv2.LINE_AA, shift=3)
            # zoomed out the lines are see-through so the artwork still shows
            a = 0.55 if s < 2.5 else 0.85
            img = cv2.addWeighted(lines, a, img, 1 - a, 0)
        r = O.MARK_DIAMETER_MM / 2 * s
        for i, (mx, my) in enumerate(sh.marks):
            O.draw_mark(img, (mx - x0) * s, (my - y0) * s, r, O.mark_color(sh, i))
        ok, buf = cv2.imencode(".png", img)
        self._img = tk.PhotoImage(data=base64.b64encode(buf.tobytes()))
        px, py = self.off[0] + x0 * s, self.off[1] + y0 * s
        c.create_rectangle(self.off[0] + 3, self.off[1] + 3, self.off[0] + sh.W * s + 3, self.off[1] + sh.H * s + 3,
                           fill="#b0b0b0", outline="")
        c.create_image(px, py, image=self._img, anchor="nw")
        if getattr(sh, "trim", False) and self.show_cut.get():
            c.create_rectangle(self.off[0], self.off[1], self.off[0] + sh.W * s, self.off[1] + sh.H * s,
                               outline="#005aff", width=2)        # trim line
        else:
            c.create_rectangle(self.off[0], self.off[1], self.off[0] + sh.W * s, self.off[1] + sh.H * s,
                               outline="#333")
        if not self.full and self.show_art.get():
            c.create_text(10, 10, anchor="nw", text="Drawing artwork…", fill="#555")
        # ask for a sharp picture of this view once zooming stops
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(350, self._want_sharp)

    def _want_sharp(self):
        self._after = None
        if self.sheet is None or not self.full or not self.show_art.get():
            return
        if self.scale <= self.full[1] * 1.05:
            return               # the whole-sheet picture is sharp enough
        x0, y0, x1, y1 = self._visible_mm()
        if self.sharp and self.sharp[3] == self.token:
            sx0, sy0, sx1, sy1 = self.sharp[1]
            if sx0 <= x0 and sy0 <= y0 and sx1 >= x1 and sy1 >= y1 and self.sharp[2] >= self.scale * 0.9:
                return
        pad = 0.15 * max(x1 - x0, y1 - y0)
        rect = (max(0, x0 - pad), max(0, y0 - pad), min(self.sheet.W, x1 + pad), min(self.sheet.H, y1 + pad))
        self.renderer.request("view", self.sheet, rect, self.scale, self.token)

    def _poll(self):
        changed = False
        try:
            while True:
                job, token, img, rect, ppm = self.inbox.get_nowait()
                if token != self.token:
                    continue
                if job == "full":
                    self.full = (img, ppm)
                else:
                    self.sharp = (img, rect, ppm, token)
                changed = True
        except queue.Empty:
            pass
        if changed:
            self.redraw()
        self.after(60, self._poll)
