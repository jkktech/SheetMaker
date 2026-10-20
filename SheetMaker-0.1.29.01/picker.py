"""
picker.py - "Pick cut line" window.

Shows the design with every stroked line, and a list of the kinds of line in the
file (colour, thickness as printed, layer). Tick the cut line in the list, or click
a line in the picture to tick or untick its kind. Picked kinds are drawn thick.
"""

import base64
import threading
import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np
import pymupdf

import designs as DS

PICK_BGR = (0, 160, 255)         # picked lines: bright orange
OTHER_BGR = (255, 120, 0)        # other strokes: blue, thin


class CutPicker(tk.Toplevel):
    def __init__(self, master, path, words, cut_rgb, current=None, on_ok=None, on_cancel=None):
        super().__init__(master)
        self.title("Pick the cut line – " + path.replace("\\", "/").split("/")[-1])
        self.geometry("1100x720")
        self.minsize(800, 500)
        self.transient(master)
        self.path = path
        self.on_ok = on_ok
        self.on_cancel = on_cancel
        self.styles = []
        self.vars = []
        self.page_img = None
        self.page_mm = (1, 1)
        self._img = None
        self.current = set(current) if current else None

        main = ttk.Frame(self, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(main, background="#d9d9d9", highlightthickness=0, cursor="hand2")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Button-1>", self._click)
        side = ttk.Frame(main, padding=(10, 0, 0, 0))
        side.grid(row=0, column=1, sticky="ns")
        ttk.Label(side, text="Lines in this file", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(side, text="Tick the cut line, or click a line in the picture.\n"
                             "Picked lines are drawn thick orange.\n"
                             "Lines that look alike but differ in colour,\n"
                             "thickness or layer are listed separately.",
                  foreground="#555", justify="left").pack(anchor="w", pady=(2, 8))
        self.listbox = ttk.Frame(side)
        self.listbox.pack(fill="both", expand=True, anchor="n")
        self.msg = ttk.Label(side, text="Reading the file…", foreground="#555", wraplength=300, justify="left")
        self.msg.pack(anchor="w", pady=(8, 0))
        b = ttk.Frame(side)
        b.pack(fill="x", pady=(10, 0))
        self.ok_btn = ttk.Button(b, text="Use as cut line", command=self._ok, state="disabled")
        self.ok_btn.pack(side="right")
        ttk.Button(b, text="Cancel", command=self._cancel).pack(side="right", padx=6)
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        threading.Thread(target=self._load, args=(words, cut_rgb), daemon=True).start()

    # ------------------------------------------------------------ loading
    def _load(self, words, cut_rgb):
        try:
            styles = DS.survey_strokes(self.path, words, cut_rgb)
            doc = pymupdf.open(self.path)
            page = doc[0]
            if page.rotation:
                page.set_rotation(0)
            r = page.rect
            ppm = min(5.0, 2400 / max(r.width, r.height) * 72 / 25.4)
            m = ppm * 25.4 / 72
            pix = page.get_pixmap(matrix=pymupdf.Matrix(m, m), alpha=False)
            img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR if pix.n == 3 else cv2.COLOR_GRAY2BGR)
            # fade the artwork so the lines stand out
            img = cv2.addWeighted(img, 0.45, np.full_like(img, 255), 0.55, 0)
            size = (r.width * 25.4 / 72, r.height * 25.4 / 72)
            doc.close()
            self.after(0, self._loaded, styles, img, size, None)
        except Exception as e:
            self.after(0, self._loaded, [], None, (1, 1), str(e))

    def _loaded(self, styles, img, size, err):
        if not self.winfo_exists():
            return
        if err:
            self.msg.config(text="Could not read the file: " + err, foreground="#b00000")
            return
        self.styles, self.page_img, self.page_mm = styles, img, size
        if not styles:
            self.msg.config(text="This file has no stroked lines at all. The cut line may be a filled shape or "
                                 "outlined text; save it as a stroke (no fill) in your design program, or "
                                 "add a separate cut file.", foreground="#b00000")
            self.redraw()
            return
        for i, e in enumerate(styles):
            on = (e["key"] in self.current) if self.current is not None else e["auto"]
            v = tk.BooleanVar(value=on)
            v.trace_add("write", lambda *a: self._changed())
            self.vars.append(v)
            row = ttk.Frame(self.listbox)
            row.pack(fill="x", anchor="w", pady=1)
            sw = tk.Canvas(row, width=26, height=14, highlightthickness=1, highlightbackground="#888")
            rgb = e["rgb"]
            fill = "#%02x%02x%02x" % tuple(int(max(0, min(1, c)) * 255) for c in rgb) if rgb else "#cccccc"
            sw.create_rectangle(0, 0, 28, 16, fill=fill, outline="")
            if not rgb:
                sw.create_text(13, 7, text="spot", font=("Segoe UI", 6))
            sw.pack(side="left", padx=(0, 6))
            cdesc, width, layer = e["key"]
            txt = f"{_nice_colour(e)}, {width:g} pt" + (f", layer '{layer}'" if layer else "")
            txt += f"  –  {e['count']} line" + ("s" if e["count"] != 1 else "")
            ttk.Checkbutton(row, text=txt, variable=v).pack(side="left")
        self._changed()

    # ------------------------------------------------------------ drawing
    def _picked(self):
        return {e["key"] for e, v in zip(self.styles, self.vars) if v.get()}

    def _changed(self):
        n = len(self._picked())
        self.ok_btn.config(state="normal" if n else "disabled")
        if n:
            self.msg.config(text="The picked lines become the cut line and are left out of the print.",
                            foreground="#555")
        else:
            self.msg.config(text="Nothing picked yet.", foreground="#555")
        self.redraw()

    def _fit(self):
        cw, ch = max(50, self.canvas.winfo_width()), max(50, self.canvas.winfo_height())
        W, H = self.page_mm
        s = min((cw - 20) / W, (ch - 20) / H)
        return s, ((cw - W * s) / 2, (ch - H * s) / 2)

    def redraw(self):
        c = self.canvas
        c.delete("all")
        if self.page_img is None:
            return
        s, (ox, oy) = self._fit()
        W, H = self.page_mm
        w, h = max(1, int(W * s)), max(1, int(H * s))
        img = cv2.resize(self.page_img, (w, h), interpolation=cv2.INTER_AREA)
        picked = self._picked()
        for e in sorted(self.styles, key=lambda e: e["key"] in picked):
            on = e["key"] in picked
            for p in e["polys"]:
                cv2.polylines(img, [np.round(p * s * 8).astype(np.int32)], False, PICK_BGR if on else OTHER_BGR,
                              3 if on else 1, lineType=cv2.LINE_AA, shift=3)
        ok, buf = cv2.imencode(".png", img)
        self._img = tk.PhotoImage(data=base64.b64encode(buf.tobytes()))
        c.create_image(ox, oy, image=self._img, anchor="nw")
        c.create_rectangle(ox, oy, ox + w, oy + h, outline="#333")

    def _click(self, ev):
        if not self.styles:
            return
        s, (ox, oy) = self._fit()
        p = np.array([(ev.x - ox) / s, (ev.y - oy) / s])
        best, bi = 8.0 / s, None          # within 8 screen pixels
        for i, e in enumerate(self.styles):
            for poly in e["polys"]:
                d = _dist_to_polyline(p, poly)
                if d < best:
                    best, bi = d, i
        if bi is not None:
            self.vars[bi].set(not self.vars[bi].get())

    def _cancel(self):
        self.destroy()
        if self.on_cancel:
            self.on_cancel()

    def _ok(self):
        picked = self._picked()
        if not picked:
            return
        self.destroy()
        if self.on_ok:
            self.on_ok(picked)


def _nice_colour(e):
    kind, spot, rgb = e["kind"], e["spot"], e["rgb"]
    if kind == "Spot":
        return f"spot colour '{spot}'"
    if rgb is None:
        return kind
    r, g, b = rgb
    names = {(1, 0, 1): "magenta", (1, 0, 0): "red", (0, 0, 0): "black", (0, 0, 1): "blue", (0, 1, 0): "green",
             (0, 1, 1): "cyan", (1, 1, 0): "yellow", (1, 1, 1): "white"}
    for k, n in names.items():
        if max(abs(a - b) for a, b in zip(rgb, k)) < 0.12:
            return f"{n} ({kind})"
    return "#%02x%02x%02x (%s)" % (int(r * 255), int(g * 255), int(b * 255), kind)


def _dist_to_polyline(p, poly):
    a, b = poly[:-1], poly[1:]
    ab = b - a
    L = (ab ** 2).sum(1)
    t = np.clip(((p - a) * ab).sum(1) / np.where(L == 0, 1, L), 0, 1)
    proj = a + ab * t[:, None]
    return float(np.sqrt(((proj - p) ** 2).sum(1)).min()) if len(a) else float(np.hypot(*(poly[0] - p)))
