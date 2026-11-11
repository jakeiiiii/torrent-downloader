"""Light/dark theme and text size for the whole app (window, dialogs, tray menu).

Theme uses Qt's own color scheme switch (Qt 6.8+), so the native Windows 11
style draws itself light or dark; "system" follows the Windows setting, live.
Text size sets the application font; widgets that don't set their own font
pick it up immediately.
"""
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from app.cli_args import log

THEMES = {"system": "Same as Windows", "light": "Light", "dark": "Dark"}
FONT_MIN, FONT_MAX = 7, 28

_SCHEMES = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}


class Appearance(QObject):
    changed = pyqtSignal()   # after theme or text size changed

    def __init__(self, app, settings):
        super().__init__()
        self._app = app
        self._settings = settings
        size = app.font().pointSizeF()
        self.default_size = round(size) if size > 0 else 9

    @property
    def font_size(self) -> int:
        return self._settings.font_size or self.default_size

    def apply(self, why: str = ""):
        theme = self._settings.theme
        self._app.styleHints().setColorScheme(_SCHEMES.get(theme, Qt.ColorScheme.Unknown))
        font = QFont(self._app.font())
        font.setPointSize(self.font_size)
        self._app.setFont(font)
        if why:
            log(f"APPEARANCE ({why}): theme {theme}, text size {self.font_size} pt"
                + (" (default)" if not self._settings.font_size else ""))
        self.changed.emit()

    def set_theme(self, theme: str):
        if theme in THEMES and theme != self._settings.theme:
            self._settings.theme = theme
            self.apply("theme changed")

    def step_font(self, delta: int):
        size = max(FONT_MIN, min(FONT_MAX, self.font_size + delta))
        if size != self.font_size:
            self._settings.font_size = 0 if size == self.default_size else size
            self.apply("text size changed")

    def reset_font(self):
        if self._settings.font_size:
            self._settings.font_size = 0
            self.apply("text size reset")
