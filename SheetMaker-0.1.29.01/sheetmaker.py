"""
SheetMaker - lay out print & cut designs on sheets for LaserVision.

Add your designs (PDF or PDF-compatible .ai), set how many of each, press Arrange.
Every sheet comes out as a print PDF and a cut PDF with LaserVision registration marks.
"""

import base64
import json
import os
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np

import designs as DS
import nest as N
import output as O
from picker import CutPicker
from preview import SheetView
import preview as PV
import scraps as SC
import store as ST
import thumbs as TH

VERSION = "0.1.29.01"
HERE = os.path.dirname(os.path.abspath(__file__))
# the settings file next to "Start SheetMaker.bat". With SheetMaker on a network drive every computer
# uses this same file; changes from the others are picked up every few seconds (see store.py).
OWN_SETTINGS_PATH = os.environ.get("SHEETMAKER_SETTINGS") or os.path.join(os.path.dirname(HERE),
                                                                          "sheetmaker_settings.json")
SETTINGS_NAME = "sheetmaker_settings.json"

DEFAULTS = {
    "max_w_in": 23.0,
    "max_h_in": 35.0,
    "spacing_mm": 5.0,
    "edge_mm": 6.0,
    "round_in": 0.25,
    "allow_rotate": True,
    "trim": True,
    "cut_color": "#ff00ff",
    "cut_words": "cut",
    "resolution_mm": 1.0,
    "last_folder": "",
    "scraps": [],
    "materials": [],
}


def _normalize(s):
    s["scraps"] = SC.normalize(s.get("scraps"))
    return s


STORE = ST.Store(OWN_SETTINGS_PATH, DEFAULTS, normalize=_normalize)


def load_settings():
    return STORE.load()


def save_settings(s):
    """Saves through the store, which keeps other computers' changes; s is updated in place
    with the merged result (so it also picks up their changes)."""
    merged = STORE.save(s)
    if merged is not s:
        s.clear()
        s.update(merged)


def _fmt_setting(v):
    return f"{v:g}" if isinstance(v, float) else str(v)


WATCH_MS = 2500            # how often the settings file is checked for changes from other computers



def _make_thumb(d):
    """The hover picture for the design list (made while loading, in the background)."""
    try:
        d.thumb_png = TH.render(d)
    except Exception:
        d.thumb_png = None
    d._thumb_img = None



def hex_to_rgb(h):
    h = (h or "").strip().lstrip("#")
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None


def inch(mm):
    return mm / 25.4


class App:
    def __init__(self, root):
        self.root = root
        self.s = load_settings()
        self.designs = []          # Design objects (with .qty, .spacing, .allow_rotate)
        self.sheets = []
        self.q = queue.Queue()
        self.busy = False
        self.cancel_flag = False
        self.preview_img = None
        root.title(f"SheetMaker {VERSION}")
        root.geometry("1250x780")
        root.minsize(980, 620)
        self._tip = None           # hover pop-up with a design's thumbnail
        self._tip_row = None
        self._tip_job = None
        self._build()
        self.root.after(100, self._poll)
        self.root.after(WATCH_MS, self._watch_settings)

    # ------------------------------------------------------------ layout

    def _build(self):
        st = ttk.Style()
        try:
            st.theme_use("vista" if sys.platform == "win32" else "clam")
        except tk.TclError:
            pass
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=0)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        left = ttk.Frame(main)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = ttk.Frame(main)
        right.grid(row=0, column=1, sticky="nsew")

        # ---- designs
        box = ttk.LabelFrame(left, text="1. Designs", padding=6)
        box.pack(fill="both", expand=True)
        cols = ("qty", "spacing", "rotate", "size", "cut")
        self.tree = ttk.Treeview(box, columns=cols, show="tree headings", height=6, selectmode="extended")
        self.tree.heading("#0", text="Design")
        self.tree.column("#0", width=200)
        for c, t, w in (("qty", "Qty", 50), ("spacing", "Spacing", 70), ("rotate", "Turn", 45),
                        ("size", "Size (in)", 90), ("cut", "Cut line from", 190)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center" if c != "cut" else "w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self._edit_cell)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_notes())
        self.tree.bind("<Motion>", self._hover)
        self.tree.bind("<Leave>", lambda e: self._left_tree())
        for ev in ("<ButtonPress>", "<MouseWheel>", "<KeyPress>"):
            self.tree.bind(ev, lambda e: self._hide_tip(), add="+")
        ttk.Label(box, text="Double-click Qty or Spacing to change it (all selected rows at once), Turn to allow/forbid.",
                  foreground="#666").pack(anchor="w", pady=(2, 0))
        b = ttk.Frame(box)
        b.pack(fill="x", pady=(6, 0))
        ttk.Button(b, text="Add files…", command=self.add_files).pack(side="left")
        ttk.Button(b, text="Pick cut line…", command=self.pick_cut_line).pack(side="left", padx=4)
        ttk.Button(b, text="Set cut file…", command=self.set_cut_file).pack(side="left")
        ttk.Button(b, text="Layers…", command=self.make_layers).pack(side="left", padx=(4, 0))
        ttk.Label(b, text="").pack(side="left", padx=2)
        ttk.Button(b, text="Remove", command=self.remove).pack(side="left")
        ttk.Button(b, text="Clear", command=self.clear).pack(side="left", padx=4)
        self.notes = ttk.Label(box, text="", foreground="#a05000", wraplength=560, justify="left")
        self.notes.pack(anchor="w", pady=(4, 0))

        # ---- settings
        sb = ttk.LabelFrame(left, text="2. Sheet and spacing", padding=6)
        sb.pack(fill="x", pady=(8, 0))
        self.v = {}

        def row(r, label, key, unit, width=7):
            ttk.Label(sb, text=label).grid(row=r, column=0, sticky="w", pady=2, padx=(0, 6))
            var = tk.StringVar(value=f"{self.s[key]:g}" if isinstance(self.s[key], float) else str(self.s[key]))
            self.v[key] = var
            ttk.Entry(sb, textvariable=var, width=width).grid(row=r, column=1, sticky="w")
            ttk.Label(sb, text=unit, foreground="#666").grid(row=r, column=2, sticky="w", padx=4)

        ttk.Label(sb, text="Largest sheet").grid(row=0, column=0, sticky="w")
        fr = ttk.Frame(sb)
        fr.grid(row=0, column=1, columnspan=2, sticky="w")
        self.v["max_w_in"] = tk.StringVar(value=f"{self.s['max_w_in']:g}")
        self.v["max_h_in"] = tk.StringVar(value=f"{self.s['max_h_in']:g}")
        ttk.Entry(fr, textvariable=self.v["max_w_in"], width=6).pack(side="left")
        ttk.Label(fr, text=" x ").pack(side="left")
        ttk.Entry(fr, textvariable=self.v["max_h_in"], width=6).pack(side="left")
        ttk.Label(fr, text=" in   (leftovers go on the smallest sheet that fits)", foreground="#666").pack(side="left")
        row(1, "Spacing between pieces", "spacing_mm", "mm  (default for every design; change per design above)")
        row(2, "Margin at sheet edges", "edge_mm", "mm")
        row(3, "Round custom sheet sizes up to", "round_in", "in")
        row(4, "Cut line colour", "cut_color", "or a layer / spot colour whose name contains the word below", 9)
        row(5, "Cut layer / spot colour name", "cut_words", "e.g. cut  (matches Cut, CutContour, Thru-cut)", 12)
        self.v_rot = tk.BooleanVar(value=bool(self.s["allow_rotate"]))
        rf6 = ttk.Frame(sb)
        rf6.grid(row=6, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Checkbutton(rf6, text="Allow pieces to be turned (90° steps)", variable=self.v_rot).pack(side="left")
        self.v_trim = tk.BooleanVar(value=bool(self.s.get("trim", True)))
        ttk.Checkbutton(rf6, text="Trim line around each sheet (blue, cut last)", variable=self.v_trim,
                        command=self._trim_changed).pack(side="left", padx=(16, 0))
        self.v_white = tk.BooleanVar(value=False)            # off every time the app starts
        wf = ttk.Frame(sb)
        wf.grid(row=7, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(wf, text="All dots white (dark material)", variable=self.v_white,
                        command=self._dots_changed).pack(side="left")
        ttk.Label(wf, text="  off: black, white only where a dot lands on dark print",
                  foreground="#666").pack(side="left")
        sf = ttk.Frame(sb)
        sf.grid(row=9, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.v_scrap = tk.BooleanVar(value=False)            # off every time the app starts
        ttk.Checkbutton(sf, text="Use scrap sheets first", variable=self.v_scrap,
                        command=self._invalidate).pack(side="left")
        ttk.Button(sf, text="Scrap list…", command=self.edit_scraps).pack(side="left", padx=6)
        self.scrap_info = ttk.Label(sf, text=SC.summary(self.s.get("scraps", [])), foreground="#666")
        self.scrap_info.pack(side="left")
        self.share_info = ttk.Label(sf, text="", foreground="#666")
        self.share_info.pack(side="left", padx=(6, 0))
        self._scrap_win = None
        self._show_share_state()

        # ---- run
        rb = ttk.LabelFrame(left, text="3. Arrange and save", padding=6)
        rb.pack(fill="x", pady=(8, 0))
        top = ttk.Frame(rb)
        top.pack(fill="x")
        ttk.Label(top, text="Job name").pack(side="left")
        self.v_job = tk.StringVar()
        ttk.Entry(top, textvariable=self.v_job, width=28).pack(side="left", padx=4)
        ttk.Label(top, text="Material").pack(side="left", padx=(10, 0))
        self.v_mat = tk.StringVar()                          # empty every time the app starts
        self.cb_mat = ttk.Combobox(top, textvariable=self.v_mat, width=12, values=self._material_list(),
                                   state="readonly")
        self.cb_mat.pack(side="left", padx=4)
        self.v_mat.trace_add("write", lambda *a: self._material_changed())
        b = ttk.Frame(rb)
        b.pack(fill="x", pady=(6, 0))
        self.btn_arrange = ttk.Button(b, text="Arrange", command=self.arrange)
        self.btn_arrange.pack(side="left")
        self.btn_again = ttk.Button(b, text="New mark spots", command=lambda: self.arrange(new_seed=True))
        self.btn_again.pack(side="left", padx=4)
        self.btn_cancel = ttk.Button(b, text="Cancel", command=self.cancel, state="disabled")
        self.btn_cancel.pack(side="left")
        self.btn_save = ttk.Button(b, text="Save print + cut files…", command=self.save, state="disabled")
        self.btn_save.pack(side="right")
        self.v_previews = tk.BooleanVar(value=False)        # off every time the app starts
        ttk.Checkbutton(b, text="Save preview pictures", variable=self.v_previews).pack(side="right", padx=8)
        self.prog = ttk.Progressbar(rb, maximum=1.0)
        self.prog.pack(fill="x", pady=(6, 0))
        self.status = ttk.Label(rb, text="Add your designs to start.", foreground="#333")
        self.status.pack(anchor="w")

        # ---- results / preview
        rf = ttk.LabelFrame(right, text="Sheets", padding=6)
        rf.pack(fill="both", expand=True)
        self.sheet_list = tk.Listbox(rf, height=5, exportselection=False, font=("Segoe UI", 10))
        self.sheet_list.pack(fill="x")
        self.sheet_list.bind("<<ListboxSelect>>", lambda e: self._draw_preview())
        self.summary = ttk.Label(rf, text="", foreground="#333", justify="left")
        self.summary.pack(anchor="w", pady=(4, 2))
        tb = ttk.Frame(rf)
        tb.pack(fill="x")
        self.view = SheetView(rf)
        ttk.Checkbutton(tb, text="Artwork", variable=self.view.show_art, command=self.view.redraw).pack(side="left")
        ttk.Checkbutton(tb, text="Cut lines", variable=self.view.show_cut, command=self.view.redraw).pack(
            side="left", padx=8)
        ttk.Label(tb, text="Background").pack(side="left", padx=(6, 4))
        self._swatches = {}
        for name, (b, g, r) in PV.BACKGROUNDS.items():
            sw = tk.Frame(tb, width=18, height=18, background="#%02x%02x%02x" % (r, g, b), cursor="hand2",
                          highlightthickness=2, highlightbackground="#bbbbbb")
            sw.pack(side="left", padx=2)
            sw.bind("<Button-1>", lambda e, n=name: self._set_background(n))
            self._swatches[name] = sw
        self._mark_swatch()
        ttk.Label(tb, text="Scroll: zoom · drag: move · double-click: fit", foreground="#666").pack(side="right")
        # two-print sheets only: each print on / off, Print 2 see-through (check lining up and mirroring)
        self.layer_bar = lb = ttk.Frame(rf)
        ttk.Label(lb, text="Two prints:").pack(side="left")
        for text, var in (("Print 1", self.view.show_p1), ("Print 2", self.view.show_p2),
                          ("Print 2 at 50%", self.view.p2_half)):
            ttk.Checkbutton(lb, text=text, variable=var, command=self.view.redraw).pack(side="left", padx=(8, 0))
        self.view.pack(fill="both", expand=True, pady=(4, 0))
        self.seed = random.randrange(1 << 30)

    # ------------------------------------------------------------ settings

    def _read_settings(self):
        s = dict(self.s)
        try:
            for k in ("max_w_in", "max_h_in", "spacing_mm", "edge_mm", "round_in"):
                s[k] = float(self.v[k].get().replace(",", "."))
        except ValueError:
            raise ValueError("Please enter numbers for the sheet size, spacing, margin and rounding.")
        if s["max_w_in"] <= 0 or s["max_h_in"] <= 0:
            raise ValueError("The largest sheet size must be more than 0.")
        if s["spacing_mm"] < 0 or s["edge_mm"] < 0 or s["round_in"] < 0:
            raise ValueError("Spacing, margin and rounding can't be negative.")
        s["cut_color"] = self.v["cut_color"].get().strip()
        if s["cut_color"] and hex_to_rgb(s["cut_color"]) is None:
            raise ValueError("Cut line colour must look like #ff00ff (or be left empty).")
        s["cut_words"] = self.v["cut_words"].get().strip()
        s["allow_rotate"] = bool(self.v_rot.get())
        s["trim"] = bool(self.v_trim.get())
        self.s = s
        save_settings(s)
        return s

    def _words(self):
        return tuple(w for w in self.s.get("cut_words", "cut").replace(",", " ").split() if w)

    # ------------------------------------------------------------ designs

    def add_files(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(
            title="Add designs (print + cut in one file, or a print file and a cut file)",
            initialdir=self.s.get("last_folder") or None,
            filetypes=[("PDF or Illustrator", "*.pdf *.ai"), ("All files", "*.*")])
        if not paths:
            return
        try:
            self._read_settings()
        except ValueError as e:
            messagebox.showerror("SheetMaker", str(e))
            return
        self.s["last_folder"] = os.path.dirname(paths[0])
        save_settings(self.s)
        self._run_bg("Reading files…", self._load_job, list(paths))

    def _load_job(self, paths):
        # several pages in one file: each page is loaded as its own design
        paths, names = DS.split_pages(paths, progress=lambda m: self.q.put(("progress", m, None)))
        pairs, lonely = DS.pair_files(paths)
        out, errs = [], []
        for i, (p, c) in enumerate(pairs):
            self.q.put(("progress", f"Reading {os.path.basename(p)}…", i / max(1, len(pairs))))
            try:
                d = DS.Design(p, c, qty=1, words=self._words(), cut_rgb=hex_to_rgb(self.s["cut_color"]),
                              allow_no_cut=True)
                d.name = names.get(p, d.name)
                d.spacing = None
                d.allow_rotate = True
                _make_thumb(d)
                out.append(d)
            except DS.DesignError as e:
                errs.append(str(e))
        for c in lonely:
            # a cut-only file with no print file to pair with: a blank, cut without printing
            try:
                d = DS.Design(c, None, qty=1, blank=True)
                d.name = names.get(c, d.name)
                d.spacing = None
                d.allow_rotate = True
                _make_thumb(d)
                out.append(d)
            except DS.DesignError as e:
                errs.append(str(e))
        return ("loaded", out, errs)

    def make_layers(self):
        """Pages of one artboard used together: a cut page, Print 2 (top, gets the dots) and
        optionally Print 1 (printed first, underneath). Each page can be mirrored in place."""
        sel = self._selected()
        if self.busy:
            return
        if len(sel) < 2:
            messagebox.showinfo("Layers", "Select the pages that belong together (Ctrl-click), for example "
                                          "\"ornament p1\", \"p2\" and \"p3\": one cut page and one or two print "
                                          "pages of the same size. Then press Layers… again.")
            return
        if any(isinstance(d, DS.LayeredDesign) for d in sel):
            messagebox.showinfo("Layers", "One of the selected rows is already made from layers.")
            return
        sel.sort(key=lambda d: self.designs.index(d))
        ROLES = ("Cut", "Print 1 (first)", "Print 2 (on top)", "Not used")
        MIRRORS = ("No", "Left ↔ right", "Top ↕ bottom")
        cov = [DS.ink_coverage(d.print_path) for d in sel]
        cut_i = int(np.argmin(cov))                     # the page with the least ink is the cut line
        prints = [i for i in range(len(sel)) if i != cut_i]
        guess = {cut_i: "Cut"}
        if len(prints) == 1:
            guess[prints[0]] = "Print 2 (on top)"
        else:
            guess[prints[0]] = "Print 1 (first)"
            guess[prints[1]] = "Print 2 (on top)"
        win = tk.Toplevel(self.root)
        win.title("Layers")
        win.transient(self.root)
        win.resizable(False, False)
        f = ttk.Frame(win, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="What is each page? Print 1 prints first, Print 2 on top of it (the dots go "
                          "on Print 2, the side the laser camera sees).\nThe sheet then gets two print files, "
                          "_P1 and _P2, in the same place. Mirror flips a page in its own place.",
                  justify="left", wraplength=620).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        role_v, mir_v = [], []
        for r, d in enumerate(sel, 1):
            ttk.Label(f, text=d.name).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=2)
            rv = tk.StringVar(value=guess.get(r - 1, "Not used"))
            ttk.Combobox(f, textvariable=rv, values=ROLES, state="readonly", width=16).grid(row=r, column=1, pady=2)
            ttk.Label(f, text="Mirror").grid(row=r, column=2, padx=(12, 4))
            mv = tk.StringVar(value="No")
            ttk.Combobox(f, textvariable=mv, values=MIRRORS, state="readonly", width=13).grid(row=r, column=3, pady=2)
            role_v.append(rv)
            mir_v.append(mv)
        name_v = tk.StringVar(value=re.sub(r"\s+p\d+$", "", sel[0].name))
        nf = ttk.Frame(f)
        nf.grid(row=len(sel) + 1, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Label(nf, text="Name").pack(side="left")
        ttk.Entry(nf, textvariable=name_v, width=40).pack(side="left", padx=6)
        res = {}

        def ok():
            roles = {}
            for d, rv, mv in zip(sel, role_v, mir_v):
                r = rv.get()
                if r == "Not used":
                    continue
                if r in roles:
                    messagebox.showerror("Layers", f"Two pages are set to {r}.", parent=win)
                    return
                roles[r] = (d.print_path, {"No": DS.MIRROR_NONE, "Left ↔ right": DS.MIRROR_LR,
                                           "Top ↕ bottom": DS.MIRROR_TB}[mv.get()])
            if "Cut" not in roles or "Print 2 (on top)" not in roles:
                messagebox.showerror("Layers", "Choose one Cut page and a Print 2 (on top) page "
                                               "(Print 1 is only for a second print underneath).", parent=win)
                return
            res["roles"] = roles
            win.destroy()

        b = ttk.Frame(f)
        b.grid(row=len(sel) + 2, column=0, columnspan=4, sticky="e", pady=(12, 0))
        ttk.Button(b, text="OK", command=ok).pack(side="left")
        ttk.Button(b, text="Cancel", command=win.destroy).pack(side="left", padx=6)
        win.grab_set()
        win.wait_window()
        if "roles" not in res:
            return
        roles = res["roles"]
        try:
            nd = DS.LayeredDesign(name_v.get().strip() or sel[0].name, roles["Cut"], roles["Print 2 (on top)"],
                                  roles.get("Print 1 (first)"))
        except DS.DesignError as e:
            messagebox.showerror("Layers", str(e))
            return
        nd.qty = max(d.qty for d in sel)
        nd.spacing = sel[0].spacing
        _make_thumb(nd)
        i = self.designs.index(sel[0])
        self.designs = [d for d in self.designs if d not in sel]
        self.designs.insert(i, nd)
        self._refresh()
        self._invalidate()
        self.tree.selection_set(str(id(nd)))

    def set_cut_file(self):
        """One cut file for the selected design(s). Several selected (e.g. the pages of a multi-page print
        file with no cut line): all of them get the same cut file. A cut file with exactly as many pages
        as designs selected can go page by page instead."""
        sel = self._selected()
        if not sel or self.busy:
            messagebox.showinfo("SheetMaker", "Select one or more designs first.")
            return
        layered = [d.name for d in sel if isinstance(d, DS.LayeredDesign)]
        blanks = [d.name for d in sel if getattr(d, "blank", False)]
        if layered:
            messagebox.showinfo("SheetMaker", ", ".join(layered) + (" is" if len(layered) == 1 else " are") +
                                " made from pages (Layers…): the cut line comes from the cut page. Remove it and "
                                "use Layers… again to change it." + ("\n\nThe others are left as they are."
                                                                       if len(layered) < len(sel) else ""))
        if blanks:
            messagebox.showinfo("SheetMaker", ", ".join(blanks) + (" is a blank" if len(blanks) == 1 else
                                                                   " are blanks") +
                                " (cut only): all lines are cut and nothing is printed." +
                                ("\n\nThe others get the cut file." if len(blanks) + len(layered) < len(sel)
                                 else ""))
        sel = [d for d in sel if not isinstance(d, DS.LayeredDesign) and not getattr(d, "blank", False)]
        if not sel:
            return
        title = f"Cut file for {sel[0].name}" if len(sel) == 1 else f"One cut file for {len(sel)} designs"
        p = filedialog.askopenfilename(title=title, initialdir=os.path.dirname(sel[0].print_path),
                                       filetypes=[("PDF or Illustrator", "*.pdf *.ai"), ("All files", "*.*")])
        if not p:
            return
        try:
            import pymupdf
            n_pages = len(pymupdf.open(p))
        except Exception:
            n_pages = 1
        by_page = False
        if n_pages > 1 and len(sel) > 1 and n_pages == len(sel):
            by_page = messagebox.askyesno(
                "SheetMaker", f"{os.path.basename(p)} has {n_pages} pages, one for each selected design.\n\n"
                              "Yes: page by page (1st design → page 1, 2nd → page 2 …, in list order)\n"
                              "No: page 1 for all of them")
        elif n_pages > 1:
            messagebox.showinfo("SheetMaker", f"{os.path.basename(p)} has {n_pages} pages: page 1 is used"
                                              + (" for all of them." if len(sel) > 1 else "."))
        words, rgb = self._words(), hex_to_rgb(self.s["cut_color"])
        order = sorted(sel, key=self.designs.index)

        def work():
            pages = [p]
            if n_pages > 1:
                pages, _ = DS.split_pages([p])
            out, errs = [], []
            for k, d in enumerate(order):
                self.q.put(("progress", f"Reading cut file for {d.name} ({k + 1} of {len(order)})…",
                            k / len(order)))
                cut = pages[k] if by_page else pages[0]
                try:
                    nd = DS.Design(d.print_path, cut, qty=d.qty, words=words, cut_rgb=rgb)
                except DS.DesignError as e:
                    errs.append(f"{d.name}: {e}")
                    continue
                nd.spacing, nd.allow_rotate = d.spacing, d.allow_rotate
                nd.name = d.name
                nd.source = "separate cut file: " + os.path.basename(p) + \
                    (f" p{k + 1}" if by_page else "")
                _make_thumb(nd)
                out.append((d, nd))
            return ("replaced_many", out, errs)

        self._run_bg("Reading cut file…", work)

    def pick_cut_line(self):
        sel = self._selected()
        if len(sel) != 1 or self.busy:
            messagebox.showinfo("SheetMaker", "Select one design first.")
            return
        d = sel[0]
        if isinstance(d, DS.LayeredDesign):
            messagebox.showinfo("SheetMaker", f"{d.name} is made from pages (Layers…): its cut line comes from "
                                              "its cut page. Remove it and use Layers… again to change it.")
            return
        if getattr(d, "blank", False):
            messagebox.showinfo("SheetMaker", f"{d.name} is a blank (cut only): all of its lines are cut and "
                                              "nothing is printed.")
            return
        if d.cut_path and not d.needs_cut:
            if not messagebox.askyesno(
                    "SheetMaker", f"{d.name} uses a separate cut file, so every line in that file is cut.\n\n"
                                  "Pick the cut line from the print file instead?"):
                return
        self._open_picker(d.print_path, d)

    def _open_picker(self, path, old, then=None):
        try:
            self._read_settings()
        except ValueError as e:
            messagebox.showerror("SheetMaker", str(e))
            return

        def ok(picked):
            def work():
                nd = DS.Design(path, None, qty=old.qty if old else 1, words=self._words(),
                               cut_rgb=hex_to_rgb(self.s["cut_color"]), pick=picked)
                nd.spacing = old.spacing if old else None
                if old:
                    nd.name = old.name
                nd.allow_rotate = old.allow_rotate if old else True
                _make_thumb(nd)
                return ("replaced", old, nd) if old else ("loaded", [nd], [])
            self._run_bg("Reading the picked cut line…", work)
            if then:
                self.root.after(300, then)

        CutPicker(self.root, path, self._words(), hex_to_rgb(self.s["cut_color"]),
                  current=(old.pick if old is not None and not old.cut_path else None), on_ok=ok, on_cancel=then)

    def _selected(self):
        ids = self.tree.selection()
        return [d for d in self.designs if str(id(d)) in ids]

    def remove(self):
        for d in self._selected():
            self.designs.remove(d)
        self._refresh()
        self._invalidate()

    def clear(self):
        self.designs = []
        self._refresh()
        self._invalidate()

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())
        for d in self.designs:
            sp = f"{self.s['spacing_mm']:g} mm" if d.spacing is None else f"{d.spacing:g} mm *"
            self.tree.insert("", "end", iid=str(id(d)), text=d.name, tags=("nocut",) if d.needs_cut else (("blank",) if getattr(d, "blank", False) else ()),
                             values=(d.qty, sp, "yes" if d.allow_rotate else "no",
                                     f"{inch(d.size_mm[0]):.2f} x {inch(d.size_mm[1]):.2f}", d.source))
        self.tree.tag_configure("nocut", foreground="#b05000")
        self.tree.tag_configure("blank", foreground="#555555")
        if self.designs and not self.v_job.get().strip():
            self.v_job.set(_job_name(self.designs[0].name))
        n = sum(d.qty for d in self.designs)
        if not self.busy and any(d.needs_cut for d in self.designs):
            self.status.config(text="Orange designs have no cut line yet: select one and use 'Pick cut line…' "
                                    "or 'Set cut file…'.")
        elif not self.busy:
            self.status.config(text=f"{len(self.designs)} design(s), {n} piece(s). Press Arrange." if self.designs
                               else "Add your designs to start.")

    def _show_notes(self):
        sel = self._selected()
        notes = []
        for d in sel:
            notes += [f"{d.name}: {n}" for n in d.notes]
        self.notes.config(text="\n".join(notes))

    def _edit_cell(self, event):
        if self.busy:
            return
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        d = next((x for x in self.designs if str(id(x)) == row), None)
        if d is None:
            return
        if col == "#3":         # turn
            d.allow_rotate = not d.allow_rotate
            self._refresh()
            self._invalidate()
            return
        if col not in ("#1", "#2"):
            return
        x, y, w, h = self.tree.bbox(row, col)
        cur = str(d.qty) if col == "#1" else ("" if d.spacing is None else f"{d.spacing:g}")
        e = ttk.Entry(self.tree, width=8)
        e.insert(0, cur)
        e.select_range(0, "end")
        e.place(x=x, y=y, width=w, height=h)
        e.focus_set()

        def done(_=None):
            val = e.get().strip().replace(",", ".")
            e.destroy()
            # with several designs selected (and this one among them) the value goes to all of them
            sel = self._selected()
            targets = sel if d in sel and len(sel) > 1 else [d]
            try:
                if col == "#1":
                    q = max(0, int(float(val)))
                    for t in targets:
                        t.qty = q
                else:
                    sp = None if val == "" else max(0.0, float(val))
                    for t in targets:
                        t.spacing = sp
            except ValueError:
                return
            self._refresh()
            self._invalidate()

        e.bind("<Return>", done)
        e.bind("<FocusOut>", done)
        e.bind("<Escape>", lambda _: e.destroy())

    # ------------------------------------------------------------ arrange

    def _invalidate(self):
        if self.sheets:
            self.sheets = []
            self.sheet_list.delete(0, "end")
            self.summary.config(text="")
            self.view.clear()
            self.btn_save.config(state="disabled")

    def arrange(self, new_seed=False):
        if self.busy:
            return
        if not self.designs or not any(d.qty > 0 for d in self.designs):
            messagebox.showinfo("SheetMaker", "Add at least one design with a quantity above 0.")
            return
        missing = [d.name for d in self.designs if d.needs_cut and d.qty > 0]
        if missing:
            messagebox.showinfo("SheetMaker", "These designs have no cut line yet:\n\n   " + "\n   ".join(missing) +
                                "\n\nSelect each one and use 'Pick cut line…' or 'Set cut file…' "
                                "(or set its quantity to 0).")
            return
        try:
            s = self._read_settings()
        except ValueError as e:
            messagebox.showerror("SheetMaker", str(e))
            return
        self._refresh()
        if new_seed:
            self.seed = random.randrange(1 << 30)
        cfg = dict(res=float(s.get("resolution_mm", 1.0)), spacing=s["spacing_mm"], edge=s["edge_mm"],
                   max_w=s["max_w_in"] * 25.4, max_h=s["max_h_in"] * 25.4, round=s["round_in"] * 25.4,
                   allow_rotate=s["allow_rotate"])
        mat = " ".join(self.v_mat.get().split())
        if self.v_scrap.get():
            if not mat:
                messagebox.showinfo("SheetMaker", "Which material is this job on? Choose it in the "
                                                  "Material box, so only scraps of that material are used.")
                self.cb_mat.focus_set()
                return
            cfg["scraps"] = [dict(id=x["id"], w=float(x["w_in"]) * 25.4, h=float(x["h_in"]) * 25.4,
                                  count=int(x["qty"]), note=x.get("note", ""), material=x.get("material", ""))
                             for x in self.s.get("scraps", [])
                             if x.get("use", True) and int(x["qty"]) > 0
                             and SC.mat_key(x.get("material")) == SC.mat_key(mat)]
            if not cfg["scraps"]:
                messagebox.showinfo("SheetMaker", f"There are no ticked {mat} scrap sheets in the scrap list. "
                                                  "Arranging on new sheets only.")
        if mat and SC.mat_key(mat) not in {SC.mat_key(m) for m in self.s.get("materials", [])}:
            self.s["materials"] = self.s.get("materials", []) + [mat]
            save_settings(self.s)
            self.cb_mat["values"] = self._material_list()
        self.job_material = mat
        self.cfg = cfg
        self._scraps_taken = False
        self._leftover_keep = {}                 # sheet number -> True (keep) / False (trash)
        seed = self.seed

        def work():
            t = time.time()
            # two-print designs (Print 1 + Print 2) never share a sheet with single-print designs
            groups = [g for g in ([d for d in self.designs if not DS.is_layered(d)],
                                  [d for d in self.designs if DS.is_layered(d)]) if any(x.qty > 0 for x in g)]
            sheets, c = [], dict(cfg)
            for g in groups:
                got = N.plan(g, c, progress=lambda m, f: self.q.put(("progress", m, f)),
                             cancel=lambda: self.cancel_flag, seed=seed)
                sheets += got
                if c.get("scraps"):                 # scraps used by this group aren't there for the next
                    used = {}
                    for sh in got:
                        if sh.kind == "scrap" and sh.scrap:
                            used[sh.scrap["id"]] = used.get(sh.scrap["id"], 0) + 1
                    c = dict(c, scraps=[dict(x, count=int(x["count"]) - used.get(x["id"], 0))
                                        for x in c["scraps"] if int(x["count"]) - used.get(x["id"], 0) > 0])
            for sh in sheets:
                if O.blanks_only(sh):
                    sh.marks = []           # nothing printed on this sheet, so nothing to line up with
            return ("arranged", sheets, time.time() - t)

        self._run_bg("Arranging…", work, cancellable=True)

    def cancel(self):
        self.cancel_flag = True

    def _show_sheets(self, sheets, secs):
        self.sheets = sheets
        self._label_sheets()
        sp = self.s["spacing_mm"]
        self.view.set_spacing(lambda d: sp if d.spacing is None else d.spacing)
        self.sheet_list.delete(0, "end")
        n = len(sheets)
        for i, sh in enumerate(sheets, 1):
            kind = _kind_text(sh)
            self.sheet_list.insert("end", f"Sheet {i} of {n}:  {inch(sh.W):.2f} x {inch(sh.H):.2f} in  "
                                          f"({kind})  –  {len(sh.placements)} pieces"
                                          + ("  –  blanks only, no print" if O.blanks_only(sh) else "")
                                          + ("  –  2 prints (P1 + P2)" if O.is_layered_sheet(sh) else ""))
        total = sum(len(sh.placements) for sh in sheets)
        self.status.config(text=f"{total} pieces on {n} sheet(s), arranged in {secs:.0f} s.")
        if sheets:
            self.sheet_list.selection_set(0)
        self.btn_save.config(state="normal" if sheets else "disabled")
        self._draw_preview()

    # ------------------------------------------------------------ scraps

    def edit_scraps(self):
        if self._scrap_win is not None and self._scrap_win.winfo_exists():
            self._scrap_win.lift()
            return
        self._scrap_win = SC.ScrapWindow(self.root, self.s.get("scraps", []), self._scraps_changed,
                                         share_text=self._share_text,
                                         known_materials=self.s.get("materials", []))

    def _set_background(self, name):
        """Preview only: the colour of the material behind the artwork."""
        self.view.background.set(name)
        self._mark_swatch()
        self.view.redraw()

    def _mark_swatch(self):
        cur = self.view.background.get()
        for name, sw in self._swatches.items():
            sw.config(highlightbackground="#1a73e8" if name == cur else "#bbbbbb",
                      highlightthickness=3 if name == cur else 1)

    def _material_list(self):
        return SC.materials(self.s.get("scraps", []), self.s.get("materials", []))

    def _material_changed(self):
        self.scrap_info.config(text=SC.summary(self.s.get("scraps", []), self.v_mat.get()))
        # dark material: white dots (and back to automatic when switching away again)
        mk = SC.mat_key(self.v_mat.get())
        if mk in ("black", "white"):
            self._set_background(mk.capitalize())
        dark = SC.is_dark(self.v_mat.get())
        if dark and not self.v_white.get():
            self.v_white.set(True)
            self._white_by_material = True
            self._dots_changed()
        elif not dark and getattr(self, "_white_by_material", False):
            self.v_white.set(False)
            self._white_by_material = False
            self._dots_changed()
        if self.v_scrap.get():
            self._invalidate()

    def _scraps_changed(self, lst):
        self.s["scraps"] = lst
        save_settings(self.s)                   # merged with other computers' changes
        self._scraps_shown()
        if self.v_scrap.get():
            self._invalidate()

    def _scraps_shown(self):
        """Show self.s's scrap list everywhere: summary line, material box, scrap window."""
        lst = self.s.get("scraps", [])
        self.scrap_info.config(text=SC.summary(lst, self.v_mat.get()))
        self.cb_mat["values"] = self._material_list()
        if self._scrap_win is not None and self._scrap_win.winfo_exists():
            self._scrap_win.set_scraps(lst)
        self._show_share_state()

    def _rewrite_summary(self):
        if not getattr(self, "_last_save", None):
            return
        out, job, sheets, previews = self._last_save
        if sheets is not self.sheets:
            return
        try:
            _write_summary(out, job, sheets, previews, getattr(self, "job_material", ""),
                           getattr(self, "_leftover_keep", {}))
        except OSError:
            pass

    def _offer_scrap_removal(self):
        """After saving: two separate questions. 1) take the used scraps off the list?
        2) add the leftover pieces? (each with its own tick box, as some are too small to keep)"""
        used, leftovers = {}, []
        for i, sh in enumerate(self.sheets, 1):
            if sh.kind == "scrap" and sh.scrap:
                used[sh.scrap["id"]] = used.get(sh.scrap["id"], 0) + 1
                if sh.scrap.get("leftover"):
                    leftovers.append((i, sh.scrap["id"], sh.scrap["leftover"]))
        if not used or getattr(self, "_scraps_taken", False):
            return
        self._scraps_taken = True                 # ask once per arrangement
        lst = self.s.get("scraps", [])
        by_id = {x["id"]: x for x in lst}
        lines = [f"   {k} x {SC.describe(by_id[sid])}" for sid, k in used.items() if sid in by_id]
        new, sheet_of = [], {}
        for i, sid, (w, h) in leftovers:
            self._leftover_keep[i] = False      # trash unless ticked below
            if sid not in by_id:
                continue
            src = by_id[sid]
            new.append({"id": SC.new_id(), "material": src.get("material", ""),
                        "w_in": _floor_in(w / 25.4), "h_in": _floor_in(h / 25.4),
                        "qty": 1, "note": src.get("note", ""), "use": True})
            sheet_of[new[-1]["id"]] = i
        changed = False
        # 1) used scraps
        if lines and messagebox.askyesno("SheetMaker", "This job uses these scrap sheets:\n\n" + "\n".join(lines) +
                                         "\n\nTake them off your scrap list?"):
            for x in lst:
                if x["id"] in used:
                    x["qty"] = max(0, int(x["qty"]) - used[x["id"]])
            lst = [x for x in lst if int(x["qty"]) > 0]
            changed = True
        # 2) leftover pieces, each with its own tick box
        if new:
            keep = SC.ask_leftovers(self.root, new)
            for n in keep:
                self._leftover_keep[sheet_of[n["id"]]] = True
            for n in keep:                          # same material, size and note already listed: add to it
                for x in lst:
                    if SC.mat_key(x.get("material")) == SC.mat_key(n["material"]) and x.get("note", "") == n["note"] \
                            and abs(float(x["w_in"]) - n["w_in"]) < 1e-6 and abs(float(x["h_in"]) - n["h_in"]) < 1e-6:
                        x["qty"] = int(x["qty"]) + 1
                        break
                else:
                    lst.append(n)
            changed = changed or bool(keep)
        if not changed:
            return
        self.s["scraps"] = lst
        save_settings(self.s)
        self._scraps_shown()

    # ------------------------------------------------------------ design thumbnails (hover)

    def _hover(self, e):
        row = self.tree.identify_row(e.y)
        if not row or self.tree.identify_column(e.x) != "#0":
            self._hide_tip()
            return
        if row == self._tip_row:
            return
        self._hide_tip()
        self._tip_row = row
        self._tip_job = self.root.after(350, lambda: self._show_tip(row))

    def _left_tree(self):
        """Pointer left the list (a pop-up showing up can send a false 'left' too, so check)."""
        try:
            under = self.root.winfo_containing(self.root.winfo_pointerx(), self.root.winfo_pointery())
        except (tk.TclError, KeyError):
            under = None
        if under is not self.tree:
            self._hide_tip()

    def _hide_tip(self):
        if self._tip_job:
            self.root.after_cancel(self._tip_job)
            self._tip_job = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
        self._tip_row = None

    def _show_tip(self, row):
        self._tip_job = None
        d = next((x for x in self.designs if str(id(x)) == row), None)
        if d is None:
            return
        if getattr(d, "thumb_png", None) is None and not self.busy:
            _make_thumb(d)
        png = getattr(d, "thumb_png", None)
        if not png:
            return
        if getattr(d, "_thumb_img", None) is None:
            d._thumb_img = tk.PhotoImage(data=TH.as_tk_data(png))
        tip = tk.Toplevel(self.root)
        tip.withdraw()                     # placed first, then shown (never pops up under the pointer)
        tip.wm_overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        fr = tk.Frame(tip, background="#ffffff", highlightthickness=1, highlightbackground="#888888")
        fr.pack()
        tk.Label(fr, image=d._thumb_img, background="#ffffff").pack(padx=4, pady=(4, 2))
        cap = f"{d.name}   {inch(d.size_mm[0]):.2f} x {inch(d.size_mm[1]):.2f} in"
        if d.needs_cut:
            cap += "   (no cut line yet)"
        elif getattr(d, "blank", False):
            cap += "   (blank, cut only)"
        tk.Label(fr, text=cap, background="#ffffff", foreground="#333", font=("Segoe UI", 9),
                 wraplength=max(240, d._thumb_img.width())).pack(padx=4, pady=(0, 4))
        tip.update_idletasks()
        x, y = self.root.winfo_pointerx() + 18, self.root.winfo_pointery() + 14
        w, h = tip.winfo_reqwidth(), tip.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        if x + w > sw:
            x = max(0, self.root.winfo_pointerx() - w - 18)
        if y + h > sh:
            y = max(0, sh - h - 4)
        tip.geometry(f"+{x}+{y}")
        tip.deiconify()
        self._tip = tip
        self._tip_row = row

    # ------------------------------------------------------------ shared settings

    def _watch_settings(self):
        """Every few seconds: did another computer change the settings file?"""
        try:
            new = STORE.poll(self.s)
            if new is not None and new is not self.s:
                self._apply_settings(new)
            self._show_share_state()
        except Exception:
            pass
        finally:
            self.root.after(WATCH_MS, self._watch_settings)

    def _apply_settings(self, new, force=False):
        """Show settings read from the file. A box you're changing right now is left alone."""
        old = self.s
        self.s = new
        same = ST._same
        for k, var in self.v.items():
            if k in new and (force or (not same(old.get(k), new.get(k)) and
                                       var.get().strip() == _fmt_setting(old.get(k)))):
                var.set(_fmt_setting(new[k]))
        for k, var in (("allow_rotate", self.v_rot), ("trim", self.v_trim)):
            if k in new and (force or (old.get(k) != new.get(k) and bool(var.get()) == bool(old.get(k)))):
                var.set(bool(new[k]))
        if bool(old.get("trim")) != bool(new.get("trim")) or force:
            for sh in self.sheets:
                sh.trim = bool(self.v_trim.get())
            self.view.redraw()
        if force or not same(old.get("spacing_mm"), new.get("spacing_mm")):
            if not self.busy:
                self._refresh()
        if force or not same(old.get("scraps"), new.get("scraps")) or not same(old.get("materials"),
                                                                                new.get("materials")):
            self._scraps_shown()

    def _share_text(self):
        if not STORE.reachable:
            return (f"Settings file can't be reached: {STORE.path}\n"
                    "Changes are kept and saved as soon as it is back.", "#c00000")
        return (f"Settings file: {STORE.path}  (changes from other computers show up within a few seconds)",
                "#666666")

    def _show_share_state(self):
        if not STORE.reachable:
            self.share_info.config(text="\u26a0 settings file can't be reached", foreground="#c00000")
        else:
            self.share_info.config(text="")
        if self._scrap_win is not None and self._scrap_win.winfo_exists():
            self._scrap_win.show_share()

    def _trim_changed(self):
        self.s["trim"] = bool(self.v_trim.get())
        save_settings(self.s)
        for sh in self.sheets:
            sh.trim = self.s["trim"]
        self.view.redraw()

    def _dots_changed(self):
        """Dot colour switched: no re-arranging needed, just redraw."""
        if not self.sheets or self.busy:
            return
        self._label_sheets()
        sp = self.s["spacing_mm"]
        self.view.set_spacing(lambda d: sp if d.spacing is None else d.spacing)   # drops cached pictures
        self._draw_preview()

    def _label_sheets(self):
        """File name printed on each sheet's edge (shown in the preview, written on save)."""
        job = self._file_job()
        n = len(self.sheets)
        for i, sh in enumerate(self.sheets, 1):
            sh.edge_mm = self.s["edge_mm"]
            sh.mark_mode = "white" if self.v_white.get() else "auto"
            sh.trim = bool(self.v_trim.get())
            sh.mark_colors = None
            sh.label = O.sheet_base(job, i, n, sh)

    def _draw_preview(self):
        sel = self.sheet_list.curselection()
        if not sel or not self.sheets:
            self.view.clear()
            self.layer_bar.pack_forget()
            return
        sh = self.sheets[sel[0]]
        if O.is_layered_sheet(sh):
            self.layer_bar.pack(fill="x", pady=(4, 0), before=self.view)
        else:
            self.layer_bar.pack_forget()
        self.view.show(sh)
        lines = [f"{inch(sh.W):.2f} x {inch(sh.H):.2f} in   ({sh.W:.0f} x {sh.H:.0f} mm)"]
        for d, k in sh.count_by_design().items():
            lines.append(f"   {k} x {d.name}")
        self.summary.config(text="\n".join(lines))

    # ------------------------------------------------------------ save

    def save(self):
        if not self.sheets or self.busy:
            return
        job = self._file_job()
        start = self.s.get("last_folder") or os.path.dirname(self.designs[0].print_path)
        folder = filedialog.askdirectory(title="Save the sheets in which folder?", initialdir=start)
        if not folder:
            return
        out = os.path.join(folder, job + " sheets")
        if os.path.exists(out) and os.listdir(out):
            if not messagebox.askyesno("SheetMaker", f"'{out}' already has files in it.\n"
                                                     "Files with the same names will be replaced. Continue?"):
                return
        os.makedirs(out, exist_ok=True)
        default_sp = self.s["spacing_mm"]
        self._label_sheets()            # current job name + dot colour setting
        sheets = self.sheets
        previews = bool(self.v_previews.get())
        self._last_save = (out, job, sheets, previews)

        def work():
            srcs = {}
            written = []
            n = len(sheets)
            for i, sh in enumerate(sheets, 1):
                self.q.put(("progress", f"Writing sheet {i} of {n}…", (i - 1) / n))
                written += O.write_sheet(sh, i, n, out, job,
                                         lambda d: default_sp if d.spacing is None else d.spacing, srcs,
                                         make_preview=previews)
            _write_summary(out, job, sheets, previews, getattr(self, "job_material", ""),
                           getattr(self, "_leftover_keep", {}))
            for x in srcs.values():
                x.close()
            unlabeled = [i for i, sh in enumerate(sheets, 1) if getattr(sh, "label_placed", True) is False]
            return ("saved", out, len(written), unlabeled)

        self._run_bg("Saving…", work)

    def _file_job(self):
        """Start of every file name: the job name (\"job\" when empty)."""
        name = _safe(self.v_job.get().strip()) if self.v_job.get().strip() else ""
        return name or "job"

    def _run_bg(self, msg, fn, *args, cancellable=False):
        self.busy = True
        self.cancel_flag = False
        self.status.config(text=msg)
        self.prog["value"] = 0
        for b in (self.btn_arrange, self.btn_again, self.btn_save):
            b.config(state="disabled")
        self.btn_cancel.config(state="normal" if cancellable else "disabled")
        self.root.config(cursor="watch")

        def run():
            try:
                self.q.put(fn(*args))
            except N.NestCancelled:
                self.q.put(("cancelled",))
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.q.put(("error", str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _done(self):
        self.busy = False
        self.root.config(cursor="")
        self.btn_arrange.config(state="normal")
        self.btn_again.config(state="normal")
        self.btn_cancel.config(state="disabled")
        self.btn_save.config(state="normal" if self.sheets else "disabled")

    def _poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                kind = m[0]
                if kind == "progress":
                    self.status.config(text=m[1])
                    if m[2] is not None:
                        self.prog["value"] = m[2]
                    continue
                self._done()
                self.prog["value"] = 1.0 if kind in ("arranged", "saved", "loaded", "replaced", "replaced_many") else 0
                if kind == "loaded":
                    _, new, errs = m
                    self.designs += new
                    self._refresh()
                    self._invalidate()
                    if errs:
                        messagebox.showwarning("SheetMaker", "\n\n".join(errs))
                    waiting = [d.name for d in new if d.needs_cut]
                    if waiting:
                        self.status.config(text=f"{len(waiting)} design(s) have no cut line yet: select one and "
                                                "use 'Pick cut line…' or 'Set cut file…' before Arrange.")
                elif kind == "replaced":
                    _, old, new = m
                    i = self.designs.index(old)
                    self.designs[i] = new
                    self._refresh()
                    self._invalidate()
                elif kind == "replaced_many":
                    _, pairs, errs = m
                    for old, new in pairs:
                        if old in self.designs:
                            self.designs[self.designs.index(old)] = new
                    self._refresh()
                    self._invalidate()
                    self.status.config(text=f"Cut file set for {len(pairs)} design(s).")
                    if errs:
                        messagebox.showwarning("SheetMaker", "\n\n".join(errs))
                elif kind == "arranged":
                    self._show_sheets(m[1], m[2])
                elif kind == "saved":
                    self.status.config(text=f"Saved {m[2]} files to {m[1]}")
                    if m[3]:
                        messagebox.showwarning("SheetMaker", "No free edge was found for the file name on sheet(s) " +
                                               ", ".join(str(i) for i in m[3]) + ", so it was left off.")
                    if sys.platform == "win32":
                        try:
                            os.startfile(m[1])
                        except Exception:
                            pass
                    self._offer_scrap_removal()
                    self._rewrite_summary()         # LEFTOVER: Keep / Trash as just answered
                elif kind == "cancelled":
                    self.status.config(text="Cancelled.")
                elif kind == "error":
                    self.status.config(text="Something went wrong.")
                    messagebox.showerror("SheetMaker", m[1])
        except queue.Empty:
            pass
        self.root.after(100, self._poll)


def _kind_text(sh):
    if sh.kind == "scrap":
        sc = sh.scrap or {}
        bits = [b for b in (sc.get("material", ""), sc.get("note", "")) if b]
        out = "scrap: " + ", ".join(bits) if bits else "scrap"
        if sc.get("leftover"):
            pw, ph = sc["piece"]
            out += f", part of {inch(pw):g} x {inch(ph):g}"
        return out
    return "largest sheet" if sh.full else "custom size"


def _write_summary(out, job, sheets, previews, material, leftover_keep, ver=1):
    """<job>_summary.txt: per sheet MATERIAL / SCRAP / LEFTOVER, PIECES, then the files."""
    n = len(sheets)
    num = O._num
    L = [job, ""]
    for i, sh in enumerate(sheets, 1):
        if i > 1:
            L += ["", ""]
        L.append(f"SHEET {i} OF {n}: {num(inch(sh.W))} x {num(inch(sh.H))} in "
                 f"({sh.W:.1f} x {sh.H:.1f} mm), {len(sh.placements)} pieces")
        sc = sh.scrap if sh.kind == "scrap" and sh.scrap else None
        mat = (sc or {}).get("material") or material or "not set"
        if sc and sc.get("note"):
            mat += f" ({sc['note']})"
        L.append(f"    MATERIAL: {mat}")
        if sc and sc.get("piece"):
            pw, ph = sc["piece"]
            L.append(f"    SCRAP:    {num(_floor_in(inch(pw)))}x{num(_floor_in(inch(ph)))}")
        elif sc:
            L.append(f"    SCRAP:    {num(inch(sh.W))}x{num(inch(sh.H))}")
        else:
            L.append("    SCRAP:    none (new sheet)")
        if sc and sc.get("leftover"):
            lw, lh = sc["leftover"]
            what = "Keep" if leftover_keep.get(i) else "Trash"
            L.append(f"    LEFTOVER: {what} ({num(_floor_in(inch(lw)))}x{num(_floor_in(inch(lh)))})")
        else:
            L.append("    LEFTOVER: none")
        L.append("")
        for j, (d, k) in enumerate(sh.count_by_design().items()):
            L.append(("    PIECES:   " if j == 0 else "              ") + f"{k} x {d.name}")
        L.append("")
        p, c, v = O.file_names(job, i, n, sh, ver)
        names = O.print_names(job, i, n, sh, ver)
        if not names:
            L.append("    PRINT:    none (blanks only: cut directly, no dots)")
        for label, name in names:
            note = {"PRINT 1": "  (first)", "PRINT 2": "  (on top: dots)"}.get(label, "")
            L.append(f"    {label + ':':<10}{name}{note}")
        L.append(f"    CUT:      {c}")
        if previews:
            L.append(f"    PREVIEW:  {O.PREVIEW_DIR}\\{v}")
    with open(os.path.join(out, job + "_summary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def _ask_buttons(parent, title, text, buttons):
    """A small dialog with your own button texts. Returns the index of the button pressed (or None)."""
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.resizable(False, False)
    res = {"i": None}
    f = ttk.Frame(win, padding=12)
    f.pack(fill="both", expand=True)
    ttk.Label(f, text=text, justify="left", wraplength=560).pack(anchor="w")
    b = ttk.Frame(f)
    b.pack(fill="x", pady=(12, 0))
    for i, t in enumerate(buttons):
        ttk.Button(b, text=t, command=lambda i=i: (res.__setitem__("i", i), win.destroy())).pack(
            side="left" if i < len(buttons) - 1 else "right", padx=(0, 6))
    win.bind("<Escape>", lambda e: win.destroy())
    win.grab_set()
    win.wait_window()
    return res["i"]


def _floor_in(v, step=0.125):
    """Leftover sizes rounded down to 1/8 in."""
    import math
    return math.floor(v / step + 1e-6) * step


def _safe(name):
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad else c for c in name).strip() or "job"


def _job_name(design_name):
    """'drink-sticks-1' -> 'drink-sticks'."""
    import re
    n = re.sub(r"^\d{10,}_", "", design_name)
    n = re.sub(r"[-_ ]*(print|cut|\d{1,2})$", "", n, flags=re.I)
    return n or design_name


def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)      # sharp text on high-DPI screens
    except Exception:
        pass
    try:
        # its own taskbar button with the SheetMaker icon, not grouped with other Python programs
        # (LaserVision) under the Python icon
        from ctypes import windll
        windll.shell32.SetCurrentProcessExplicitAppUserModelID("SheetMaker.App")
    except Exception:
        pass
    root = tk.Tk()
    ico = os.path.join(HERE, "sheetmaker.ico")
    if sys.platform == "win32" and os.path.exists(ico):
        try:
            root.iconbitmap(default=ico)
        except tk.TclError:
            pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
