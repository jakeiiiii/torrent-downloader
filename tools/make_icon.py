"""Draw the app icon and write resources/icons/app.ico (plus app.png for previews).

    python tools/make_icon.py

Each size is drawn separately rather than scaled down from 256 px, so small
sizes (16/24 px in the tray and title bar) stay sharp: thicker strokes, and the
small "peer" dots are left out where they'd turn to mush.
"""
import os
import struct
import sys

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter,
    QPainterPath, QPen,
)

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "resources", "icons")

TOP = QColor("#14b8a6")      # teal
BOTTOM = QColor("#1d4ed8")   # blue
WHITE = QColor("#ffffff")


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)

    # Rounded-square tile with a top-to-bottom gradient.
    inset = 0.0 if size <= 24 else s * 0.03
    tile = QRectF(inset, inset, s - 2 * inset, s - 2 * inset)
    grad = QLinearGradient(0, 0, 0, s)
    grad.setColorAt(0, TOP)
    grad.setColorAt(1, BOTTOM)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    radius = s * 0.22
    p.drawRoundedRect(tile, radius, radius)

    # Down arrow: shaft + head, as one filled shape.
    cx = s / 2
    shaft_w = s * (0.20 if size <= 24 else 0.16)
    head_w = s * (0.62 if size <= 24 else 0.54)
    top_y = s * 0.17
    head_y = s * 0.45
    tip_y = s * 0.70
    arrow = QPainterPath()
    arrow.moveTo(cx - shaft_w / 2, top_y)
    arrow.lineTo(cx + shaft_w / 2, top_y)
    arrow.lineTo(cx + shaft_w / 2, head_y)
    arrow.lineTo(cx + head_w / 2, head_y)
    arrow.lineTo(cx, tip_y)
    arrow.lineTo(cx - head_w / 2, head_y)
    arrow.lineTo(cx - shaft_w / 2, head_y)
    arrow.closeSubpath()
    p.setBrush(WHITE)
    p.drawPath(arrow)

    # Base bar the arrow lands on.
    bar_h = max(2.0, s * (0.11 if size <= 24 else 0.085))
    bar_w = s * (0.66 if size <= 24 else 0.60)
    bar = QRectF(cx - bar_w / 2, s * 0.77, bar_w, bar_h)
    p.drawRoundedRect(bar, bar_h / 2, bar_h / 2)

    # Peer dots either side of the shaft (large sizes only), linked to it.
    if size >= 48:
        dot_r = s * 0.045
        pen = QPen(QColor(255, 255, 255, 150), max(1.0, s * 0.018))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        for dx, dy in ((-0.27, 0.24), (0.27, 0.24)):
            c = QPointF(cx + s * dx, s * dy)
            p.setPen(pen)
            p.drawLine(c, QPointF(cx + (shaft_w / 2) * (1 if dx > 0 else -1), s * 0.30))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(WHITE)
            p.drawEllipse(c, dot_r, dot_r)

    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


def write_ico(path: str, images: list[tuple[int, bytes]]):
    """ICO file with PNG-compressed entries (supported since Windows Vista)."""
    header = struct.pack("<HHH", 0, 1, len(images))
    entries, blobs = b"", b""
    offset = 6 + 16 * len(images)
    for size, blob in images:
        dim = 0 if size >= 256 else size  # 0 means 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        blobs += blob
        offset += len(blob)
    with open(path, "wb") as f:
        f.write(header + entries + blobs)


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841 (QPainter needs it for fonts/AA)
    os.makedirs(OUT_DIR, exist_ok=True)
    images = [(sz, png_bytes(draw(sz))) for sz in SIZES]
    write_ico(os.path.join(OUT_DIR, "app.ico"), images)
    draw(256).save(os.path.join(OUT_DIR, "app.png"))
    # Contact sheet of every size, for checking the small ones by eye.
    sheet = QImage(sum(SIZES) + 10 * (len(SIZES) + 1), 276, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#f3f4f6"))
    p = QPainter(sheet)
    x = 10
    for sz in SIZES:
        p.drawImage(x, 266 - sz, draw(sz))
        x += sz + 10
    p.end()
    sheet.save(os.path.join(OUT_DIR, "preview.png"))
    print("wrote", os.path.join(OUT_DIR, "app.ico"))


if __name__ == "__main__":
    main()
