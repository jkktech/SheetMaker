"""
thumbs.py - Small pictures of a design (artwork + cut line) for the hover pop-up in the design list.
"""

import base64

import cv2
import numpy as np
import pymupdf

MM_TO_PT = 72 / 25.4
CUT_BGR = (255, 0, 255)          # magenta, like the preview
BLANK_BGR = (90, 90, 90)


def render(d, max_px=240):
    """PNG bytes of design d: its print art around the cut line (a little bleed shown) with the
    cut line drawn on top. A blank shows only its cut lines; a design without a cut line yet
    shows the whole page."""
    x0, y0, x1, y1 = d.cut_box
    pad = max(2.0, 0.04 * max(x1 - x0, y1 - y0))
    if getattr(d, "needs_cut", False):
        pad = 0.0
    bx0, by0, bx1, by1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad
    w_mm, h_mm = max(bx1 - bx0, 1.0), max(by1 - by0, 1.0)
    s = max_px / max(w_mm, h_mm)                       # px per mm
    W, H = max(1, int(round(w_mm * s))), max(1, int(round(h_mm * s)))
    img = np.full((H, W, 3), 255, np.uint8)
    if d.print_pdf:
        doc = pymupdf.open("pdf", d.print_pdf)
        try:
            page = doc[0]
            pr = page.rect
            clip = pymupdf.Rect(bx0 * MM_TO_PT, by0 * MM_TO_PT, bx1 * MM_TO_PT, by1 * MM_TO_PT) & pr
            if not clip.is_empty:
                z = s / MM_TO_PT
                pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, alpha=False,
                                      colorspace=pymupdf.csRGB)
                a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
                ox = int(round((clip.x0 / MM_TO_PT - bx0) * s))
                oy = int(round((clip.y0 / MM_TO_PT - by0) * s))
                hh, ww = min(a.shape[0], H - oy), min(a.shape[1], W - ox)
                if hh > 0 and ww > 0:
                    img[oy:oy + hh, ox:ox + ww] = a[:hh, :ww, ::-1]
        finally:
            doc.close()
    if not getattr(d, "needs_cut", False):
        col = BLANK_BGR if getattr(d, "blank", False) else CUT_BGR
        for p in d.cut:
            pts = (np.asarray(p, float) - [bx0, by0]) * s
            cv2.polylines(img, [np.round(pts * 8).astype(np.int32)], False, col, 2 if max_px >= 200 else 1,
                          lineType=cv2.LINE_AA, shift=3)
    cv2.rectangle(img, (0, 0), (W - 1, H - 1), (200, 200, 200), 1)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes() if ok else None


def as_tk_data(png):
    return base64.b64encode(png)
