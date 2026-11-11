from PyQt6.QtWidgets import QStyledItemDelegate, QApplication, QStyle
from PyQt6.QtCore import Qt, QModelIndex, QRect
from PyQt6.QtGui import QColor, QPen, QBrush, QLinearGradient


# BitTorrent-style colors per state
_STATE_COLORS = {
    "downloading": (QColor(80, 140, 210), QColor(50, 110, 180)),   # blue gradient
    "seeding":     (QColor(80, 180, 80),  QColor(50, 150, 50)),    # green gradient
    "paused":      (QColor(180, 180, 180), QColor(150, 150, 150)), # gray gradient
    "queued":      (QColor(200, 180, 100), QColor(170, 150, 70)),  # amber gradient
    "checking":    (QColor(180, 140, 200), QColor(150, 110, 170)), # purple gradient
    "metadata":    (QColor(180, 140, 200), QColor(150, 110, 170)), # purple gradient
}
_DEFAULT_COLORS = (QColor(150, 150, 150), QColor(120, 120, 120))


class StatusBarDelegate(QStyledItemDelegate):
    """Draws a BitTorrent-style progress bar with state text overlaid."""

    def paint(self, painter, option, index: QModelIndex):
        user_data = index.data(Qt.ItemDataRole.UserRole)
        if user_data is None:
            super().paint(painter, option, index)
            return

        progress, state = user_data
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""

        painter.save()

        # Draw cell background (handle selection highlight)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        else:
            painter.fillRect(option.rect, option.palette.base())

        # Bar area with padding
        rect = option.rect.adjusted(3, 3, -3, -3)

        # Track and border follow the theme (light or dark).
        pal = option.palette
        dark = pal.window().color().lightness() < 128
        track = QColor(60, 60, 60) if dark else QColor(230, 230, 230)
        border = QColor(110, 110, 110) if dark else QColor(160, 160, 160)

        # Draw bar background (track)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(track))
        painter.drawRect(rect)

        # Draw filled portion
        if progress > 0:
            fill_width = int(rect.width() * min(progress, 1.0))
            fill_rect = QRect(rect.x(), rect.y(), fill_width, rect.height())

            colors = _STATE_COLORS.get(state, _DEFAULT_COLORS)
            gradient = QLinearGradient(fill_rect.x(), fill_rect.y(),
                                       fill_rect.x(), fill_rect.bottom())
            gradient.setColorAt(0, colors[0])
            gradient.setColorAt(1, colors[1])

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(gradient))
            painter.drawRect(fill_rect)

        # Draw border
        painter.setPen(QPen(border))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        # Text centered on the bar: white in dark mode, black in light; both
        # read on the colored fill.
        painter.setPen(QPen(QColor(240, 240, 240) if dark else QColor(0, 0, 0)))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

        painter.restore()
