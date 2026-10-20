"""
nest.py - True-shape nesting on a pixel grid, sheet sizing and registration marks.

Every copy is drawn as a filled mask of its cut line (U) and the same mask grown by
that design's spacing (D). A copy may go where
    its D touches no other copy's U and no mark,
    its U touches no other copy's D, and stays inside the sheet margin,
so two copies always end up at least max(their spacings) apart.

Two ways to place copies, the better result wins:
  * pattern: for repeated copies, a regular interlocking layout is worked out
    (single copies in a grid, or pairs turned 180 deg, rows offset to interlock)
    and the sheet is filled row by row from the top;
  * free: copies go in largest first, each at the spot that keeps the used area
    lowest (or leftmost), trying every allowed quarter turn.

Sheets: full sheets of the largest size are filled while more copies are left
than fit on one; whatever is left goes on the smallest custom sheet that holds it.

Coordinates: mm, sheet top-left = 0,0, X right, Y down.
"""

import itertools
import math
import random
import re

import cv2
import numpy as np

MARK_DIAMETER_MM = 6.35          # 0.25 in, what LaserVision expects
MARK_MIN_CLEAR_MM = 3.0          # clear ring around each mark, at least
MIN_ORIENTATION_MM = 8.0         # marks must look clearly different when the sheet is turned

PATTERN_MIN_COPIES = 3
LEFTOVER_MIN_MM = 25.4          # a scrap leftover narrower than this isn't worth keeping
FREE_SEARCH_MAX = 25             # one design: free placement is also tried for the last sheet up to this many           # repeated copies of a design worth a pattern

# layouts tried for each sheet; the one placing the most (or the smallest sheet) wins
STRATEGIES = [("pattern", None), ("bottom", None), ("bottom", (1, 3)), ("left", (0, 2))]


class NestCancelled(Exception):
    pass


def _compose(A2, A1):
    M = A2[:, :2] @ A1[:, :2]
    t = A2[:, :2] @ A1[:, 2] + A2[:, 2]
    return np.hstack([M, t.reshape(2, 1)])


def apply(A, pts):
    pts = np.asarray(pts, float)
    return pts @ A[:, :2].T + A[:, 2]


# ------------------------------------------------------------------ shapes


class Shape:
    """Masks of one design at one resolution, for each allowed quarter turn."""

    def __init__(self, design, res, spacing, allow_rotate=True, pad_mm=None):
        self.design = design
        self.res = res
        self.spacing = spacing
        pad_mm = (spacing + 3 * res) if pad_mm is None else pad_mm
        self.pad_px = int(math.ceil(pad_mm / res))
        x0, y0, x1, y1 = design.cut_box
        self.origin = (x0 - self.pad_px * res, y0 - self.pad_px * res)
        w = int(math.ceil((x1 - x0) / res)) + 2 * self.pad_px + 1
        h = int(math.ceil((y1 - y0) / res)) + 2 * self.pad_px + 1
        U = np.zeros((h, w), np.uint8)
        SH = 4
        sc = (1 << SH) / res
        org = np.array(self.origin)
        for poly in design.cut:
            pts = np.round((poly - org) * sc).astype(np.int32)
            closed = math.dist(poly[0], poly[-1]) <= 0.2
            if closed and len(poly) >= 3:
                cv2.fillPoly(U, [pts], 1, lineType=cv2.LINE_8, shift=SH)
            cv2.polylines(U, [pts], False, 1, thickness=1, lineType=cv2.LINE_8, shift=SH)
        # fill the outside outline: no nesting inside holes, no gaps inside the part
        cnts, _ = cv2.findContours(U, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(U, cnts, -1, 1, thickness=cv2.FILLED)
        # one pixel extra so grid rounding can never let shapes come closer than asked
        U = cv2.dilate(U, np.ones((3, 3), np.uint8))
        r = max(0, int(math.ceil(spacing / res)))
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        D = cv2.dilate(U, k)
        self.area = float(U.sum()) * res * res
        self.rots = [0, 1, 2, 3] if allow_rotate else [0]
        self.U = {q: np.ascontiguousarray(np.rot90(U, q)).astype(np.float32) for q in self.rots}
        self.D = {q: np.ascontiguousarray(np.rot90(D, q)).astype(np.float32) for q in self.rots}
        self.box = {}           # real pixels of U in the frame: x0, y0, x1, y1 (exclusive)
        for q in self.rots:
            ys, xs = np.nonzero(self.U[q])
            self.box[q] = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        self.frame = (w * res, h * res)       # frame size in mm, turn 0
        self._patterns = None

    def transform(self, q, tl):
        """2x3 affine: design mm -> sheet mm for turn q with the frame's top-left at tl (mm)."""
        fw, fh = self.frame
        A = np.array([[1, 0, -self.origin[0]], [0, 1, -self.origin[1]]], float)
        w, h = fw, fh
        for _ in range(q):
            # np.rot90 (counter-clockwise on screen, Y down): (x, y) -> (y, w - x)
            R = np.array([[0, 1, 0], [-1, 0, w]], float)
            A = _compose(R, A)
            w, h = h, w
        T = np.array([[1, 0, tl[0]], [0, 1, tl[1]]], float)
        return _compose(T, A)

    # -------------------------------------------------- repeating patterns

    def patterns(self, top=6):
        """Good lattices for this shape: list of dicts
        {cell: [(q, dx, dy)], v1: (x, y), v2: (x, y), density}, best first. Pixels."""
        if self._patterns is not None:
            return self._patterns
        configs = []
        for q in self.rots:
            if q in (0, 1):
                configs.append([q])
                if (q + 2) in self.rots:
                    configs.append([q, q + 2])
        out = []
        for cfg in configs:
            cell = self._make_cell(cfg)
            if cell is None:
                continue
            out += self._lattices(cell, top)
        out.sort(key=lambda p: -p["density"])
        staggered = [p for p in out if not p.get("straight")][: max(top, 1) * 2]
        self._patterns = staggered + [p for p in out if p.get("straight")]
        return self._patterns

    def _make_cell(self, qs):
        q0 = qs[0]
        if len(qs) == 1:
            return [(q0, 0, 0)]
        q1 = qs[1]
        UA, DB, UB = self.U[q0], self.D[q1], self.U[q1]
        hA, wA = UA.shape
        hB, wB = UB.shape
        canvas = np.zeros((hA + 2 * hB, wA + 2 * wB), np.float32)
        canvas[hB:hB + hA, wB:wB + wA] = UA
        R = cv2.matchTemplate(canvas, DB, cv2.TM_CCORR)          # B frame at (x, y) on canvas
        free = R < 0.5
        ax0, ay0, ax1, ay1 = self.box[q0]
        ax0, ax1, ay0, ay1 = ax0 + wB, ax1 + wB, ay0 + hB, ay1 + hB
        bx0, by0, bx1, by1 = self.box[q1]
        ys = np.arange(R.shape[0])[:, None]
        xs = np.arange(R.shape[1])[None, :]
        w = np.maximum(ax1, xs + bx1) - np.minimum(ax0, xs + bx0)
        h = np.maximum(ay1, ys + by1) - np.minimum(ay0, ys + by0)
        area = np.where(free, (w * h).astype(float), np.inf)
        if not np.isfinite(area).any():
            return None
        # several equally small pairs: prefer the one that's least elongated
        y, x = divmod(int(np.argmin(area)), area.shape[1])
        return [(q0, 0, 0), (q1, int(x) - wB, int(y) - hB)]

    def _cell_masks(self, cell):
        x0 = min(dx for _, dx, _ in cell)
        y0 = min(dy for _, _, dy in cell)
        x1 = max(dx + self.U[q].shape[1] for q, dx, _ in cell)
        y1 = max(dy + self.U[q].shape[0] for q, _, dy in cell)
        Uc = np.zeros((y1 - y0, x1 - x0), np.float32)
        Dc = np.zeros_like(Uc)
        for q, dx, dy in cell:
            u, d = self.U[q], self.D[q]
            sl = (slice(dy - y0, dy - y0 + u.shape[0]), slice(dx - x0, dx - x0 + u.shape[1]))
            Uc[sl] = np.maximum(Uc[sl], u)
            Dc[sl] = np.maximum(Dc[sl], d)
        return Uc, Dc

    def _lattices(self, cell, top):
        Uc, Dc = self._cell_masks(cell)
        h, w = Uc.shape
        canvas = np.zeros((3 * h, 3 * w), np.float32)
        canvas[h:2 * h, w:2 * w] = Uc
        R = cv2.matchTemplate(canvas, Dc, cv2.TM_CCORR)     # shape (2h+1, 2w+1), offset (x-w, y-h)
        coll = R >= 0.5

        def free(dx, dy):
            dx = np.asarray(dx)
            dy = np.asarray(dy)
            inside = (np.abs(dx) <= w) & (np.abs(dy) <= h)
            cx = np.clip(dx + w, 0, 2 * w)
            cy = np.clip(dy + h, 0, 2 * h)
            return ~(inside & coll[cy, cx])

        # v1: for every vertical shift, the smallest free step to the right
        v1 = []
        for dy in range(-h, h + 1):
            row = coll[dy + h, w + 1:]
            nz = np.nonzero(~row)[0]
            dx = int(nz[0]) + 1 if len(nz) else w + 1
            v1.append((dx, dy))
        # v2: for every horizontal shift, the smallest free step down
        v2 = []
        for dx in range(-w, w + 1):
            col = coll[h + 1:, dx + w]
            nz = np.nonzero(~col)[0]
            dy = int(nz[0]) + 1 if len(nz) else h + 1
            v2.append((dx, dy))
        a = np.array(v1, float)
        b = np.array(v2, float)
        det = np.abs(a[:, None, 0] * b[None, :, 1] - a[:, None, 1] * b[None, :, 0])
        ai = np.repeat(np.arange(len(a))[:, None], len(b), 1)
        bi = np.repeat(np.arange(len(b))[None, :], len(a), 0)
        ok = det > 0.5
        # the lattice is only valid if a few combinations are free as well
        ok &= free((2 * a[:, 0]).astype(int), (2 * a[:, 1]).astype(int))[:, None]
        ok &= free((2 * b[:, 0]).astype(int), (2 * b[:, 1]).astype(int))[None, :]
        for sgn in (1, -1):
            ok &= free((a[:, None, 0] + sgn * b[None, :, 0]).astype(int),
                       (a[:, None, 1] + sgn * b[None, :, 1]).astype(int))
        det = np.where(ok, det, np.inf)
        order = np.argsort(det, axis=None)[: top * 4]
        out, seen = [], set()
        cell_area = float(Uc.sum())
        # straight layouts, always offered even when a staggered one packs a little denser:
        # a plain grid, rows lined up along the bottom (v1 flat), columns lined up on the left (v2 upright)
        straight = []
        i0, j0 = h, w                              # index of dy = 0 in a, dx = 0 in b
        if np.isfinite(det[i0, j0]):
            straight.append((i0, j0))
        row = det[i0, :]
        if np.isfinite(row).any():
            straight.append((i0, int(np.argmin(row))))
        col = det[:, j0]
        if np.isfinite(col).any():
            straight.append((int(np.argmin(col)), j0))
        for i, j in straight:
            key = (tuple(a[i]), tuple(b[j]))
            if key in seen:
                continue
            seen.add(key)
            out.append({"cell": cell, "v1": (int(a[i, 0]), int(a[i, 1])), "v2": (int(b[j, 0]), int(b[j, 1])),
                        "density": cell_area / det[i, j], "straight": True})
        for idx in order:
            if not np.isfinite(det.flat[idx]):
                break
            i, j = ai.flat[idx], bi.flat[idx]
            key = (tuple(a[i]), tuple(b[j]))
            if key in seen:
                continue
            seen.add(key)
            out.append({"cell": cell, "v1": (int(a[i, 0]), int(a[i, 1])), "v2": (int(b[j, 0]), int(b[j, 1])),
                        "density": cell_area / det.flat[idx]})
            if len(out) >= top:
                break
        return out


# ------------------------------------------------------------------ marks


def _fit_rigid(src, dst):
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    cs, cd = src.mean(0), dst.mean(0)
    s, d = src - cs, dst - cd
    a = np.sum(s[:, 0] * d[:, 0] + s[:, 1] * d[:, 1])
    b = np.sum(s[:, 0] * d[:, 1] - s[:, 1] * d[:, 0])
    n = math.hypot(a, b) or 1.0
    M = np.array([[a / n, -b / n], [b / n, a / n]])
    t = cd - M @ cs
    return np.hstack([M, t.reshape(2, 1)])


def orientation_margin(points):
    """Same test LaserVision uses: smallest RMS mismatch (mm) between the mark
    layout and any turned / mirrored relabelling of it. Big = can't be mixed up."""
    P = np.asarray(points, float)
    n = len(P)
    best = float("inf")
    for mirror in (False, True):
        Q = P * np.array([-1, 1]) if mirror else P
        for perm in itertools.permutations(range(n)):
            if not mirror and perm == tuple(range(n)):
                continue
            src = Q[list(perm)]
            A = _fit_rigid(src, P)
            rms = float(np.sqrt(np.mean(np.sum((apply(A, src) - P) ** 2, axis=1))))
            best = min(best, rms)
    return best


def mark_points(W, H, offsets, edge, dia=MARK_DIAMETER_MM):
    inset = edge + dia / 2
    (a, b), (c, d), (e, f), (g, h) = offsets
    return [(inset + a, inset + b),            # top-left corner area
            (W - inset - c, inset + d),        # top-right
            (inset + e, H - inset - f),        # bottom-left
            (W - inset - g, H - inset - h)]    # bottom-right


def pick_offsets(rng, W, H, edge, max_shift=60.0, tries=400):
    """Random spot for each mark near its corner, different every sheet, checked so
    the sheet can't be mixed up when turned or flipped."""
    shift = max(4.0, min(max_shift, 0.18 * min(W, H)))
    best, best_m = None, -1
    for _ in range(tries):
        off = [(rng.uniform(0, shift), rng.uniform(0, shift)) for _ in range(4)]
        m = orientation_margin(mark_points(W, H, off, edge))
        if m >= MIN_ORIENTATION_MM:
            return off
        if m > best_m:
            best, best_m = off, m
    return best


# ------------------------------------------------------------------ sheet state


class Board:
    """Occupancy of one sheet. A = copies' U + marks (tested against a new D);
    B = margin + copies' D (tested against a new U)."""

    def __init__(self, W, H, marks, cfg, E):
        res = cfg["res"]
        self.res, self.E = res, E
        self.wpx, self.hpx = int(math.floor(W / res)), int(math.floor(H / res))
        shape = (self.hpx + 2 * E, self.wpx + 2 * E)
        self.A = np.zeros(shape, np.float32)
        self.B = np.ones(shape, np.float32)
        m = int(math.ceil(cfg["edge"] / res))
        self.B[E + m:E + self.hpx - m, E + m:E + self.wpx - m] = 0
        for (mx, my) in marks:
            c = (int(round(mx / res)) + E, int(round(my / res)) + E)
            cv2.circle(self.A, c, int(math.ceil((MARK_DIAMETER_MM / 2) / res)) + 1, 1, -1)
            cv2.circle(self.B, c, int(math.ceil((MARK_DIAMETER_MM / 2 + MARK_MIN_CLEAR_MM) / res)) + 1, 1, -1)
        self.used_bottom = 0       # px, sheet coords
        self.placements = []

    def fits(self, s, q, x, y):
        U, D = s.U[q], s.D[q]
        h, w = U.shape
        if x < 0 or y < 0 or y + h > self.A.shape[0] or x + w > self.A.shape[1]:
            return False
        if (self.A[y:y + h, x:x + w] * D).sum() > 0.5:
            return False
        return (self.B[y:y + h, x:x + w] * U).sum() < 0.5

    def place(self, s, q, x, y):
        U, D = s.U[q], s.D[q]
        h, w = U.shape
        sl = (slice(y, y + h), slice(x, x + w))
        self.A[sl] = np.maximum(self.A[sl], U)
        self.B[sl] = np.maximum(self.B[sl], D)
        tl = ((x - self.E) * self.res, (y - self.E) * self.res)
        self.placements.append((s.design, q, s.transform(q, tl)))
        self.used_bottom = max(self.used_bottom, y - self.E + s.box[q][3])

    def best_spot(self, s, q, mode):
        U, D = s.U[q], s.D[q]
        if U.shape[0] > self.A.shape[0] or U.shape[1] > self.A.shape[1]:
            return None
        R = cv2.matchTemplate(self.A, D, cv2.TM_CCORR) + cv2.matchTemplate(self.B, U, cv2.TM_CCORR)
        ok = R < 0.5
        if not ok.any():
            return None
        x0, y0, x1, y1 = s.box[q]
        ys = np.arange(R.shape[0])[:, None]
        xs = np.arange(R.shape[1])[None, :]
        if mode == "left":
            score = np.where(ok, (xs + x1) * 100000.0 + (ys + y1), np.inf)
        else:
            score = np.where(ok, (ys + y1) * 100000.0 + (xs + x0), np.inf)
        idx = int(np.argmin(score))
        y, x = divmod(idx, R.shape[1])
        return score.flat[idx], x, y

    def pattern_fill(self, s, pat, count, check=None):
        """Place up to count copies on the lattice, top rows first. Returns number placed."""
        cell, (ax, ay), (bx, by) = pat["cell"], pat["v1"], pat["v2"]
        H, W = self.A.shape
        # lattice origin: the cell's top-left real pixel at the margin corner
        cx0 = min(dx + s.box[q][0] for q, dx, _ in cell)
        cy0 = min(dy + s.box[q][1] for q, _, dy in cell)
        m = self.E
        det = ax * by - ay * bx
        best = None
        for phase in ((0, 0), (0.5, 0), (0, 0.5)):
            ox = m - cx0 + int(round(phase[0] * ax + phase[1] * bx))
            oy = m - cy0 + int(round(phase[0] * ay + phase[1] * by))
            # lattice indices covering the whole board
            corners = np.array([[0, 0], [W, 0], [0, H], [W, H]], float) - [ox, oy]
            inv = np.array([[by, -bx], [-ay, ax]], float) / det
            ij = corners @ inv.T
            i0, i1 = int(math.floor(ij[:, 0].min())) - 2, int(math.ceil(ij[:, 0].max())) + 2
            j0, j1 = int(math.floor(ij[:, 1].min())) - 2, int(math.ceil(ij[:, 1].max())) + 2
            spots = []
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    px, py = ox + i * ax + j * bx, oy + i * ay + j * by
                    for q, dx, dy in cell:
                        x, y = px + dx, py + dy
                        U = s.U[q]
                        if x < 0 or y < 0 or x + U.shape[1] > W or y + U.shape[0] > H:
                            continue
                        spots.append((y + s.box[q][3], x + s.box[q][0], q, x, y))
            spots.sort()
            # dry run on a copy to compare phases
            trial = Board.__new__(Board)
            trial.__dict__.update(self.__dict__)
            trial.A, trial.B, trial.placements = self.A.copy(), self.B.copy(), list(self.placements)
            n = 0
            for _, _, q, x, y in spots:
                if n >= count:
                    break
                if trial.fits(s, q, x, y):
                    trial.place(s, q, x, y)
                    n += 1
            key = (n, -trial.used_bottom)
            if best is None or key > best[0]:
                best = (key, trial)
            if check:
                check()
        trial = best[1]
        n = best[0][0]
        self.A, self.B, self.placements, self.used_bottom = trial.A, trial.B, trial.placements, trial.used_bottom
        return n


def nest_sheet(parts, shapes, W, H, marks, cfg, check=None, mode="bottom", rots=None):
    """parts: list of designs (one per copy, largest first).
    Returns (placements, leftover, used_bottom_mm, board)."""
    E = max(s.pad_px for s in shapes.values()) + 2
    board = Board(W, H, marks, cfg, E)
    todo = list(parts)
    if mode == "pattern":
        counts = {}
        for d in todo:
            counts[id(d)] = counts.get(id(d), 0) + 1
        order = sorted({id(d): d for d in todo}.values(), key=lambda d: -shapes[id(d)].area * counts[id(d)])
        for d in order:
            n = counts[id(d)]
            if n < PATTERN_MIN_COPIES:
                continue
            s = shapes[id(d)]
            best = None
            for pat in s.patterns():
                if check:
                    check()
                trial = Board.__new__(Board)
                trial.__dict__.update(board.__dict__)
                trial.A, trial.B, trial.placements = board.A.copy(), board.B.copy(), list(board.placements)
                k = trial.pattern_fill(s, pat, n, check)
                key = (k, -trial.used_bottom)
                if best is None or key > best[0]:
                    best = (key, trial)
            if best and best[0][0] > 0:
                t = best[1]
                board.A, board.B, board.placements, board.used_bottom = t.A, t.B, t.placements, t.used_bottom
                placed = best[0][0]
                kept = []
                for x in todo:
                    if x is d and placed > 0:
                        placed -= 1
                        continue
                    kept.append(x)
                todo = kept
        mode = "bottom"
    left = []
    full_fail = set()
    for d in todo:
        if check:
            check()
        if id(d) in full_fail:
            left.append(d)
            continue
        s = shapes[id(d)]
        best = None
        for q in s.rots:
            if rots is not None and q not in rots and len(s.rots) > 1:
                continue
            r = board.best_spot(s, q, mode)
            if r and (best is None or r[0] < best[0]):
                best = (r[0], q, r[1], r[2])
        if best is None:
            full_fail.add(id(d))
            left.append(d)
            continue
        board.place(s, best[1], best[2], best[3])
    return board.placements, left, board.used_bottom * board.res, board


def _bottom_marks_clear(board, pts, spacing):
    """Would marks at pts keep at least `spacing` (and the mark's clear ring) from every copy?"""
    res, E = board.res, board.E
    rr = MARK_DIAMETER_MM / 2 + max(spacing, MARK_MIN_CLEAR_MM)
    for (mx, my) in pts:
        cx, cy = mx / res + E, my / res + E
        r = rr / res + 1
        y0, y1 = int(max(0, cy - r)), int(min(board.A.shape[0], cy + r + 1))
        x0, x1 = int(max(0, cx - r)), int(min(board.A.shape[1], cx + r + 1))
        if y0 >= y1 or x0 >= x1:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        disk = (yy - cy) ** 2 + (xx - cx) ** 2 <= r * r
        if (board.A[y0:y1, x0:x1][disk] > 0).any():
            return False
    return True


def _round_up(v, unit):
    if unit <= 0:
        return v
    return math.ceil(v / unit - 1e-6) * unit


# ------------------------------------------------------------------ whole job


class Sheet:
    def __init__(self, W, H, full, kind=None):
        self.W, self.H, self.full = W, H, full
        self.kind = kind or ("full" if full else "custom")     # "scrap", "full" or "custom"
        self.scrap = None                                       # the scrap-list entry used, if any
        self.placements = []        # (design, q, affine design->sheet)
        self.marks = []

    def count_by_design(self):
        out = {}
        for d, _, _ in self.placements:
            out[d] = out.get(d, 0) + 1
        # in name order, with page numbers counted as numbers (p2 before p10)
        key = lambda d: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", d.name)]
        return dict(sorted(out.items(), key=lambda kv: key(kv[0])))


def _spacing(d, cfg):
    return cfg["spacing"] if getattr(d, "spacing", None) is None else d.spacing


def plan(designs, cfg, progress=None, cancel=None, seed=None):
    """designs: list with .qty (and optional .spacing). cfg: res, spacing, edge,
    max_w, max_h, round, allow_rotate, and optional scraps: [{id, w, h, count, note}]
    in mm, used before any new sheet. Returns list of Sheet."""
    rng = random.Random(seed)

    def check():
        if cancel and cancel():
            raise NestCancelled()

    def say(msg, frac=None):
        if progress:
            progress(msg, frac)

    edge = cfg["edge"]
    big_w, big_h = sorted((cfg["max_w"], cfg["max_h"]))       # short side, long side
    designs = [d for d in designs if d.qty > 0]
    if not designs:
        return []
    fine = dict(cfg)
    coarse = dict(cfg, res=max(cfg["res"] * 2, 2.0))
    pad_f = max(_spacing(d, cfg) for d in designs) + 3 * fine["res"]
    pad_c = max(_spacing(d, cfg) for d in designs) + 3 * coarse["res"]
    rot = lambda d: d.allow_rotate and cfg["allow_rotate"]
    say("Preparing shapes…", 0.0)
    shapes_f = {id(d): Shape(d, fine["res"], _spacing(d, cfg), rot(d), pad_f) for d in designs}
    shapes_c = {id(d): Shape(d, coarse["res"], _spacing(d, cfg), rot(d), pad_c) for d in designs}

    parts = []
    for d in designs:
        parts += [d] * int(d.qty)
    parts.sort(key=lambda d: -shapes_f[id(d)].area)
    total = len(parts)

    for d in designs:
        pl, _, _, _ = nest_sheet([d], shapes_f, big_w, big_h,
                                 mark_points(big_w, big_h, [(0, 0)] * 4, edge), fine)
        if not pl:
            raise ValueError(f"'{d.name}' ({d.size_mm[0]:.0f} x {d.size_mm[1]:.0f} mm) does not fit on the "
                             f"largest sheet with the margins, spacing and marks.")

    sheets = []
    remaining = parts
    if cfg.get("scraps"):
        remaining = _use_scraps(remaining, cfg, shapes_f, fine, rng, check, say, sheets, total)
    while remaining:
        check()
        done = total - len(remaining)
        say(f"Filling sheet {len(sheets) + 1} ({done} of {total} placed)…", 0.6 * done / total)
        off = pick_offsets(rng, big_w, big_h, edge)
        marks = mark_points(big_w, big_h, off, edge)
        best = None
        for mode, rots in STRATEGIES:
            r = nest_sheet(remaining, shapes_f, big_w, big_h, marks, fine, check, mode, rots)
            a = sum(shapes_f[id(d)].area for d, _, _ in r[0])
            key = (a, -r[2])
            if best is None or key > best[0]:
                best = (key, r)
        pl, left = best[1][0], best[1][1]
        if not pl:
            raise ValueError("Nothing more fits on a sheet - check the sizes and margins.")
        if left:
            sh = Sheet(big_w, big_h, True)
            sh.placements, sh.marks = pl, marks
            sheets.append(sh)
            remaining = left
            continue
        say(f"Finding the smallest sheet for the last {len(remaining)}…", 0.65)
        sheets.append(_smallest_sheet(remaining, shapes_c, shapes_f, cfg, coarse, fine, rng, check, say,
                                      fallback=(big_w, big_h, pl, marks)))
        remaining = []
    say("Done.", 1.0)
    return sheets


def _best_on(parts, shapes, W, H, marks, c, check):
    """Best layout of `parts` on a W x H sheet over all strategies: (placed area, result)."""
    best = None
    for mode, rots in STRATEGIES:
        r = nest_sheet(parts, shapes, W, H, marks, c, check, mode, rots)
        a = sum(shapes[id(d)].area for d, _, _ in r[0])
        key = (a, -r[2])
        if best is None or key > best[0]:
            best = (key, r)
        if not r[1]:
            break                 # everything fits: no need to try other layouts
    return best[0][0], best[1]


def _use_scraps(remaining, cfg, shapes, fine, rng, check, say, sheets, total):
    """Fill scrap sheets before any new sheet. Each round tries every scrap size still
    in stock, both ways round. If one scrap takes everything that's left, the smallest
    such scrap is used and we're done; otherwise the scrap that takes the most is used.
    Stops when the scraps run out or nothing more fits on any of them."""
    edge = cfg["edge"]
    big_w, big_h = sorted((cfg["max_w"], cfg["max_h"]))
    stock = []
    for sc in cfg["scraps"]:
        w, h = sorted((float(sc["w"]), float(sc["h"])))
        if w <= 0 or h <= 0 or int(sc.get("count", 0)) <= 0:
            continue
        if w > big_w + 1e-6 or h > big_h + 1e-6:
            continue              # bigger than the printer takes
        stock.append(dict(sc, w=w, h=h, left=int(sc["count"])))
    while remaining and any(s["left"] > 0 for s in stock):
        check()
        done = total - len(remaining)
        cands, tried = [], {}
        for s in stock:
            if s["left"] <= 0:
                continue
            for W, H in {(s["w"], s["h"]), (s["h"], s["w"])}:
                key = (round(W, 2), round(H, 2))
                if key not in tried:
                    say(f"Trying scrap {W / 25.4:.2f} x {H / 25.4:.2f} in ({done} of {total} placed)…",
                        0.5 * done / total)
                    off = pick_offsets(rng, W, H, edge)
                    marks = mark_points(W, H, off, edge)
                    if orientation_margin(marks) < MIN_ORIENTATION_MM:
                        tried[key] = None
                        continue
                    area, r = _best_on(remaining, shapes, W, H, marks, fine, check)
                    tried[key] = (area, r, marks)
                t = tried[key]
                if t and t[1][0]:
                    cands.append((t[0], not t[1][1], W * H, s, W, H, t[1], t[2]))
        if not cands:
            break
        all_fit = [c for c in cands if c[1]]
        if all_fit:
            # Everything left fits on one scrap: use only a strip of it, across its full width
            # (either way round), so the rest of the scrap stays one clean rectangle.
            say(f"Using only as much of a scrap as needed for the last {len(remaining)}…", 0.55)
            best = None
            for c in all_fit:
                s, W, H = c[3], c[4], c[5]
                r = _shrink_to_fit(remaining, shapes, W, H, cfg, fine, rng, check)
                if r is None:
                    r = (H, c[7], c[6][0])                              # can't shrink: whole scrap
                used = W * r[0]
                key = (s["w"] * s["h"], used)                           # smallest scrap first; then least used
                if best is None or key < best[0]:
                    best = (key, s, W, H, r)
            _, s, W, H, (H2, marks, placements) = best
            sh = Sheet(W, H2, False, kind="scrap")
            sh.placements, sh.marks = placements, marks
            sh.scrap = {k: s[k] for k in ("id", "note", "material") if k in s}
            sh.scrap["piece"] = (W, H)                                  # the whole scrap, mm
            if H - H2 >= LEFTOVER_MIN_MM:
                sh.scrap["leftover"] = (W, H - H2)
            sheets.append(sh)
            s["left"] -= 1
            return []
        pick = max(cands, key=lambda c: (c[0], -c[2]))                  # takes the most; smaller on a tie
        _, _, _, s, W, H, r, marks = pick
        sh = Sheet(W, H, False, kind="scrap")
        sh.placements, sh.marks = r[0], marks
        sh.scrap = {k: s[k] for k in ("id", "note", "material") if k in s}
        sh.scrap["piece"] = (W, H)
        sheets.append(sh)
        s["left"] -= 1
        remaining = r[1]
    return remaining


def _shrink_to_fit(parts, shapes, W, H_lim, cfg, fine, rng, check):
    """Shortest strip W x H (H <= H_lim) that holds all parts, dots included.
    Returns (H, marks, placements) or None."""
    edge, unit = cfg["edge"], cfg["round"]
    max_sp = max(_spacing(d, cfg) for d in parts)
    best = None
    for mode, _ in (("pattern", None), ("bottom", None)):
        off = pick_offsets(rng, W, H_lim, edge, max_shift=36.0)
        top = mark_points(W, H_lim, off, edge)[:2]
        pl, left, bottom, board = nest_sheet(parts, shapes, W, H_lim, top, fine, check, mode)
        if left:
            continue
        H = min(H_lim, _round_up(max(bottom + edge, 2 * edge + MARK_DIAMETER_MM), unit))
        lrng = random.Random(hash((round(W, 2), mode, "shrink")) & 0xffffffff)
        found = None
        while found is None:
            for o in [off] + [off[:2] + [(lrng.uniform(0, 36.0), lrng.uniform(0, 36.0)) for _ in range(2)]
                              for _ in range(60)]:
                pts = mark_points(W, H, o, edge)
                if _bottom_marks_clear(board, pts[2:], max_sp) and orientation_margin(pts) >= MIN_ORIENTATION_MM:
                    found = o
                    break
            if found is None:
                if H >= H_lim - 1e-6:
                    break
                H = min(H_lim, _round_up(H + unit, unit))
        if found is not None and (best is None or H < best[0]):
            best = (H, found, mode)
    if best is None:
        return None
    H, o, mode = best
    while True:                              # final layout with all four dots in place
        check()
        marks = mark_points(W, H, o, edge)
        for m in (mode, "pattern", "bottom"):
            pl, left, _, _ = nest_sheet(parts, shapes, W, H, marks, fine, check, m)
            if not left:
                return H, marks, pl
        if H >= H_lim - 1e-6:
            return None
        H = min(H_lim, _round_up(H + unit, unit))


def _smallest_sheet(parts, shapes_c, shapes_f, cfg, coarse, fine, rng, check, say, fallback):
    edge, unit = cfg["edge"], cfg["round"]
    big_w, big_h = sorted((cfg["max_w"], cfg["max_h"]))
    min_sp = min(_spacing(d, cfg) for d in parts)
    # The width search always runs on the coarse grid (fast); the best few widths are then
    # redone at full detail, so small gaps still come out exact.
    off = pick_offsets(rng, 200, 200, edge, max_shift=36.0)
    counts = {}
    for d in parts:
        counts[id(d)] = counts.get(id(d), 0) + 1
    if len(counts) == 1 and len(parts) > FREE_SEARCH_MAX:
        modes = [("pattern", None)]          # many copies of one design: the pattern search is enough
    else:
        modes = [("pattern", None), ("bottom", None)]
    max_sp = max(_spacing(d, cfg) for d in parts)

    def try_width(W, H_lim, shapes, c, mode):
        """Smallest height for this width: (H, mark offsets) or None. The bottom dots are
        looked for in free spots near the bottom corners before the sheet is made taller."""
        top = mark_points(W, H_lim, off, edge)[:2]
        pl, left, bottom, board = nest_sheet(parts, shapes, W, H_lim, top, c, check, mode)
        if left:
            return None
        H = _round_up(max(bottom + edge, 2 * edge + MARK_DIAMETER_MM), unit)
        lrng = random.Random(hash((round(W, 2), mode)) & 0xffffffff)
        shift = 36.0
        while H <= H_lim + 1e-6:
            tries = [off] + [off[:2] + [(lrng.uniform(0, shift), lrng.uniform(0, shift)) for _ in range(2)]
                             for _ in range(60)]
            for o in tries:
                pts = mark_points(W, H, o, edge)
                if _bottom_marks_clear(board, pts[2:], max_sp) and orientation_margin(pts) >= MIN_ORIENTATION_MM:
                    return H, o
            H = _round_up(H + max(unit, c["res"]), unit)
        return None

    min_w = _round_up(max(min(d.size_mm) for d in parts) + 2 * edge, unit)
    step = max(unit, 25.4)
    cands = []
    w = min_w
    while w <= big_h + 1e-6:
        cands.append(w)
        w = _round_up(w + step, unit)
    best = None
    found = []                                   # (area, W, mode) from the quick search
    for i, W in enumerate(cands):
        check()
        say(f"Trying sheet widths… {W / 25.4:.2f} in", 0.65 + 0.25 * i / max(1, len(cands)))
        H_lim = big_h if W <= big_w + 1e-6 else big_w
        for mode, _ in modes:
            sh_, c_ = (shapes_f, fine) if mode == "pattern" else (shapes_c, coarse)
            r = try_width(W, H_lim, sh_, c_, mode)
            if r is not None:
                found.append((W * r[0], W, mode))
            if r is not None and (best is None or W * r[0] < best[0]):
                best = (W * r[0], W, r[0], mode, r[1])
    if best is not None:
        bw = best[1]
        w = max(min_w, _round_up(bw - step, unit))
        while w <= min(big_h, bw + step) + 1e-6:
            check()
            H_lim = big_h if w <= big_w + 1e-6 else big_w
            for mode, _ in modes:
                sh_, c_ = (shapes_f, fine) if mode == "pattern" else (shapes_c, coarse)
                r = try_width(w, H_lim, sh_, c_, mode)
                if r is not None:
                    found.append((w * r[0], w, mode))
                if r is not None and w * r[0] < best[0]:
                    best = (w * r[0], w, r[0], mode, r[1])
            w = _round_up(w + unit, unit)
        # the quick search ran on a coarser grid: redo the most promising few at full detail
        if coarse is not fine:
            say("Fine-tuning the last sheet…", 0.9)
            seen, top = set(), []
            for a, W, mode in sorted(found):
                if mode == "pattern":
                    continue                     # already at full detail
                if (round(W, 2), mode) not in seen:
                    seen.add((round(W, 2), mode))
                    top.append((W, mode))
                if len(top) >= 6:
                    break
            fbest = None
            for W, mode in top:
                check()
                H_lim = big_h if W <= big_w + 1e-6 else big_w
                r = try_width(W, H_lim, shapes_f, fine, mode)
                if r is not None and (fbest is None or W * r[0] < fbest[0]):
                    fbest = (W * r[0], W, r[0], mode, r[1])
            if fbest is not None and (best[3] != "pattern" or fbest[0] < best[0]):
                best = fbest

    say("Placing the last sheet…", 0.93)
    if best is not None:
        _, W, H, mode, boff = best
        H_lim = big_h if W <= big_w + 1e-6 else big_w
        while H <= H_lim + 1e-6:
            check()
            marks = mark_points(W, H, boff, edge)
            for m, _ in [(mode, None)] + [x for x in [("pattern", None), ("bottom", None), ("left", None)]
                                          if x[0] != mode]:
                pl, left, _, _ = nest_sheet(parts, shapes_f, W, H, marks, fine, check, m)
                if not left and orientation_margin(marks) >= MIN_ORIENTATION_MM:
                    sh = Sheet(W, H, False)
                    sh.placements, sh.marks = pl, marks
                    return sh
            H = _round_up(H + unit, unit)
    W, H, pl, marks = fallback
    sh = Sheet(W, H, True)
    sh.placements, sh.marks = pl, marks
    return sh
