"""
scraps.py - The scrap sheet list: sizes of leftover material you want used up first.

Each entry: {"id", "material", "w_in", "h_in", "qty", "note", "use"} kept in the settings file.
  material = what the sheet is, e.g. "White" or "White 1/8"; a job only uses scraps whose
             material matches the job's material (capitals and extra spaces don't matter)
  use      = this scrap may be used at all (untick to keep it back)
The window shows the list grouped by material.
"""

import time
import tkinter as tk
from tkinter import messagebox, ttk


STANDARD_MATERIALS = ("Clear", "Frosted", "Black", "White")
DARK_MATERIALS = ("black",)            # job material that needs white dots


def is_dark(material):
    return mat_key(material) in DARK_MATERIALS


def new_id():
    return "s%x" % int(time.time() * 1000000)


def mat_key(m):
    """'  white   1/8 ' and 'White 1/8' are the same material."""
    return " ".join(str(m or "").lower().split())


def normalize(scraps):
    """Bring older lists up to date: before materials existed the material was written
    in the note, so an entry without a material takes its note as the material."""
    out = []
    for s in scraps or []:
        s = dict(s)
        if "material" not in s:
            note = " ".join(str(s.get("note", "")).split())
            s["material"], s["note"] = note, ""
            for m in STANDARD_MATERIALS:            # "white 1/8" -> White + note "1/8"
                if mat_key(note) == mat_key(m) or mat_key(note).startswith(mat_key(m) + " "):
                    s["material"], s["note"] = m, note[len(m):].strip()
                    break
        s.setdefault("note", "")
        s.setdefault("use", True)
        s.setdefault("id", new_id())
        out.append(s)
    return out


def materials(scraps, extra=()):
    """Material names: the standard ones first, then any others in the list."""
    seen = {}
    for m in list(STANDARD_MATERIALS) + list(extra) + [s.get("material", "") for s in scraps]:
        k = mat_key(m)
        if k and k not in seen:
            seen[k] = " ".join(str(m).split())
    std = [mat_key(m) for m in STANDARD_MATERIALS]
    return sorted(seen.values(), key=lambda m: (std.index(mat_key(m)) if mat_key(m) in std else len(std),
                                                mat_key(m)))


def summary(scraps, material=None):
    if not scraps:
        return "no scraps in the list"
    total = sum(int(s.get("qty", 0)) for s in scraps)
    if material is not None and mat_key(material):
        n = sum(int(s.get("qty", 0)) for s in scraps
                if s.get("use", True) and mat_key(s.get("material")) == mat_key(material))
        return f"{n} {' '.join(material.split())} scrap sheet{'s' if n != 1 else ''} ready to use ({total} in the list)"
    return f"{total} scrap sheet{'s' if total != 1 else ''} in the list"


def describe(s):
    out = f"{float(s['w_in']):g} x {float(s['h_in']):g} in"
    bits = [b for b in (s.get("material", ""), s.get("note", "")) if b]
    if bits:
        out += " (" + ", ".join(bits) + ")"
    return out


class ScrapWindow(tk.Toplevel):
    """Edit the scrap list. Changes are handed to on_change(list) straight away."""

    COLS = (("use", "Use", 45), ("w", "Width (in)", 75), ("h", "Height (in)", 75), ("qty", "How many", 70),
            ("note", "Note (thickness…)", 170))

    def __init__(self, master, scraps, on_change, known_materials=(), share_text=None):
        super().__init__(master)
        self.title("Scrap sheets")
        self.geometry("860x500")
        self.minsize(780, 420)
        self.transient(master)
        self.scraps = normalize(scraps)
        self.known = list(known_materials)
        self.on_change = on_change
        self.share_text = share_text
        self._editor = None          # inline edit box open right now
        self._waiting = None         # list from another computer, shown when the edit is done
        f = ttk.Frame(self, padding=8)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="With \"Use scrap sheets first\" ticked, a job uses the ticked scraps of its own material "
                          "before any new sheet.", foreground="#555", justify="left").pack(anchor="w")
        self.tree = ttk.Treeview(f, columns=[c[0] for c in self.COLS], show="tree headings", height=9,
                                 selectmode="extended")
        self.tree.heading("#0", text="Material")
        self.tree.column("#0", width=170)
        for key, title, w in self.COLS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=w, anchor="w" if key == "note" else "center")
        self.tree.tag_configure("group", font=("Segoe UI", 9, "bold"))
        self.tree.pack(fill="both", expand=True, pady=(6, 4))
        self.tree.bind("<Double-1>", self._edit)
        ttk.Label(f, text="Double-click a cell to change it. Double-click Use to tick or untick "
                          "(on a material line: the whole material).", foreground="#666").pack(anchor="w")

        add = ttk.LabelFrame(f, text="Add a scrap size", padding=6)
        add.pack(fill="x", pady=(8, 0))
        self.v_m, self.v_w, self.v_h = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.v_q, self.v_n = tk.StringVar(value="1"), tk.StringVar()
        self.v_m.set(STANDARD_MATERIALS[-1])
        ttk.Label(add, text="Material").grid(row=0, column=0, padx=(0, 2))
        self.cb = ttk.Combobox(add, textvariable=self.v_m, width=14)
        self.cb.grid(row=0, column=1)
        col = 2
        for lbl, var, w in (("W", self.v_w, 6), ("x H", self.v_h, 6), ("in   How many", self.v_q, 4),
                            ("Note", self.v_n, 14)):
            ttk.Label(add, text=lbl).grid(row=0, column=col, padx=(6, 2))
            e = ttk.Entry(add, textvariable=var, width=w)
            e.grid(row=0, column=col + 1)
            e.bind("<Return>", lambda ev: self._add())
            col += 2
        ttk.Button(add, text="Add", command=self._add).grid(row=0, column=col, padx=(8, 0))

        b = ttk.Frame(f)
        b.pack(fill="x", pady=(8, 0))
        ttk.Button(b, text="Remove selected", command=self._remove).pack(side="left")
        ttk.Button(b, text="Close", command=self.destroy).pack(side="right")
        self.share_lbl = ttk.Label(f, text="", justify="left", wraplength=820)
        self.share_lbl.pack(anchor="w", pady=(6, 0))
        self.show_share()
        self._refresh()

    # ------------------------------------------------------------ list
    def _groups(self):
        g = {}
        for s in self.scraps:
            g.setdefault(mat_key(s.get("material")), []).append(s)
        return g

    def _refresh(self):
        before = set(self.tree.get_children())
        open_ = {i for i in before if self.tree.item(i, "open")}
        self.tree.delete(*self.tree.get_children())
        groups = self._groups()
        order = [mat_key(m) for m in materials(self.scraps, self.known)]
        for k in sorted(groups, key=lambda k: order.index(k) if k in order else len(order)):
            items = sorted(groups[k], key=lambda s: -float(s["w_in"]) * float(s["h_in"]))
            name = " ".join(str(items[0].get("material") or "").split()) or "(no material)"
            uses = {bool(s.get("use", True)) for s in items}
            use = "yes" if uses == {True} else ("no" if uses == {False} else "some")
            gid = "grp:" + k
            self.tree.insert("", "end", iid=gid, text=name, open=gid in open_ or gid not in before, tags=("group",),
                             values=(use, "", "", sum(int(s["qty"]) for s in items), ""))
            for s in items:
                self.tree.insert(gid, "end", iid=s["id"], text="",
                                 values=("yes" if s.get("use", True) else "no", f"{float(s['w_in']):g}",
                                         f"{float(s['h_in']):g}", int(s["qty"]), s.get("note", "")))
        self.cb["values"] = materials(self.scraps, self.known)

    def show_share(self):
        if self.share_text:
            text, col = self.share_text()
            self.share_lbl.config(text=text, foreground=col)

    def set_scraps(self, scraps):
        """The list as it is now in the settings file (other computers' changes included)."""
        scraps = normalize(scraps)
        if self._editor is not None:
            self._waiting = scraps
            return
        if [dict(x) for x in scraps] != self.scraps:
            self.scraps = [dict(x) for x in scraps]
            self._refresh()

    def _changed(self):
        self._refresh()
        self.on_change([dict(s) for s in self.scraps])

    @staticmethod
    def _num(v, what, integer=False):
        try:
            x = float(str(v).strip().replace(",", "."))
        except ValueError:
            raise ValueError(f"{what} must be a number.")
        if integer:
            x = int(round(x))
            if x < 0:
                raise ValueError(f"{what} can't be negative.")
        elif x <= 0:
            raise ValueError(f"{what} must be more than 0.")
        return x

    def _add(self):
        mat = " ".join(self.v_m.get().split())
        if not mat:
            messagebox.showerror("Scrap sheets", "Choose the material (Clear, Frosted, Black or White).", parent=self)
            return
        try:
            w = self._num(self.v_w.get(), "Width")
            h = self._num(self.v_h.get(), "Height")
            q = self._num(self.v_q.get() or "1", "How many", integer=True)
        except ValueError as e:
            messagebox.showerror("Scrap sheets", str(e), parent=self)
            return
        if q == 0:
            return
        # use the spelling already in the list for this material
        for s in self.scraps:
            if mat_key(s.get("material")) == mat_key(mat):
                mat = s["material"]
                break
        note = self.v_n.get().strip()
        for s in self.scraps:         # same material, size and note: just add to the count
            if mat_key(s.get("material")) == mat_key(mat) and abs(float(s["w_in"]) - w) < 1e-6 \
                    and abs(float(s["h_in"]) - h) < 1e-6 and s.get("note", "") == note:
                s["qty"] = int(s["qty"]) + q
                break
        else:
            self.scraps.append({"id": new_id(), "material": mat, "w_in": w, "h_in": h, "qty": q,
                                "note": note, "use": True})
        self.v_w.set("")
        self.v_h.set("")
        self.v_q.set("1")
        self._changed()

    def _remove(self):
        ids = set()
        for i in self.tree.selection():
            if i.startswith("grp:"):
                ids |= set(self.tree.get_children(i))
            else:
                ids.add(i)
        if not ids:
            return
        self.scraps = [s for s in self.scraps if s["id"] not in ids]
        self._changed()

    def _edit(self, event):
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row:
            return
        key = "material" if col == "#0" else self.COLS[int(col[1:]) - 1][0]
        if row.startswith("grp:"):
            items = [s for s in self.scraps if "grp:" + mat_key(s.get("material")) == row]
            if key == "use":
                on = not all(s.get("use", True) for s in items)
                for s in items:
                    s["use"] = on
                self._changed()
                return "break"
            if key == "material":
                self._inline(row, col, items[0].get("material", ""),
                             lambda v: [s.__setitem__("material", " ".join(v.split())) for s in items],
                             combo=True)
                return "break"
            return
        s = next((x for x in self.scraps if x["id"] == row), None)
        if s is None:
            return
        if key == "use":
            s["use"] = not s.get("use", True)
            self._changed()
            return
        cur = {"material": s.get("material", ""), "w": f"{float(s['w_in']):g}", "h": f"{float(s['h_in']):g}",
               "qty": str(s["qty"]), "note": s.get("note", "")}[key]

        def setv(val):
            if key == "material":
                if not val.strip():
                    raise ValueError("The material can't be empty.")
                s["material"] = " ".join(val.split())
            elif key == "w":
                s["w_in"] = self._num(val, "Width")
            elif key == "h":
                s["h_in"] = self._num(val, "Height")
            elif key == "qty":
                s["qty"] = self._num(val, "How many", integer=True)
            else:
                s["note"] = val.strip()

        self._inline(row, col, cur, setv, combo=(key == "material"))

    def _inline(self, row, col, cur, setv, combo=False):
        x, y, w, h = self.tree.bbox(row, col)
        if combo:
            e = ttk.Combobox(self.tree, values=materials(self.scraps, self.known))
            e.set(cur)
        else:
            e = ttk.Entry(self.tree)
            e.insert(0, cur)
        e.select_range(0, "end")
        e.place(x=x, y=y, width=max(w, 120 if combo else w), height=h)
        e.focus_set()
        state = {"done": False}
        self._editor = e

        def finish():
            self._editor = None
            if self._waiting is not None:          # another computer changed the list meanwhile
                w, self._waiting = self._waiting, None
                self.after(10, lambda: self.set_scraps(w))

        def done(_=None):
            if state["done"]:
                return
            state["done"] = True
            val = e.get()
            e.destroy()
            self._editor = None
            try:
                setv(val)
            except ValueError as err:
                messagebox.showerror("Scrap sheets", str(err), parent=self)
                finish()
                return
            self._changed()
            finish()

        e.bind("<Return>", done)
        e.bind("<FocusOut>", lambda ev: None if combo else done())
        if combo:
            e.bind("<<ComboboxSelected>>", done)
        e.bind("<Escape>", lambda _: (state.__setitem__("done", True), e.destroy(), finish()))


def ask_leftovers(master, pieces):
    """Ask which leftover pieces go on the scrap list: one tick box each (all ticked).
    Returns the ticked pieces ([] for "Don't add" or closing the window)."""
    win = tk.Toplevel(master)
    win.title("Leftover scrap pieces")
    win.transient(master)
    win.resizable(False, False)
    f = ttk.Frame(win, padding=12)
    f.pack(fill="both", expand=True)
    ttk.Label(f, text="This job leaves these leftover pieces.\nAdd them to your scrap list?",
              justify="left").pack(anchor="w")
    ttk.Label(f, text="Untick any that are too small to keep.", foreground="#666").pack(anchor="w", pady=(2, 8))
    vars_ = []
    for p in pieces:
        v = tk.BooleanVar(value=True)
        vars_.append(v)
        ttk.Checkbutton(f, text=describe(p), variable=v).pack(anchor="w", pady=1)
    result = []
    b = ttk.Frame(f)
    b.pack(fill="x", pady=(12, 0))

    def add():
        result.extend(p for p, v in zip(pieces, vars_) if v.get())
        win.destroy()

    ttk.Button(b, text="Add ticked", command=add).pack(side="right")
    ttk.Button(b, text="Don't add", command=win.destroy).pack(side="right", padx=6)
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    win.grab_set()
    win.wait_window()
    return result
