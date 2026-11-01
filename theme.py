"""Native Qt themes with consistently readable media selection dialogs."""
import json
import os
from functools import lru_cache


class ThemeManager:
    """Persist theme names and derive styles without requiring a Qt import.

    Player palettes vary by theme; file dialogs deliberately retain their own
    light surface and dark text so platform file views keep readable contrast.
    """
    DEFAULT_THEME = "Studio"
    MAX_CONFIG_BYTES = 64 * 1024
    # The file browser deliberately uses a fixed light palette. QFileDialog's
    # file views otherwise inherit light text from a dark player theme while
    # retaining the platform's white item background.
    _FILE_DIALOG_QSS = """
QFileDialog, QFileDialog QWidget {
    background-color: #f5f7fa;
    color: #17212f;
}
QFileDialog QLabel { background: transparent; color: #17212f; }
QFileDialog QAbstractItemView {
    background: #ffffff;
    alternate-background-color: #f2f5f9;
    color: #17212f;
    selection-background-color: #b8d8fa;
    selection-color: #102033;
    border: 1px solid #a7b4c4;
    border-radius: 4px;
    outline: 0;
}
QFileDialog QAbstractItemView::item {
    color: #17212f;
    background: transparent;
    border: 1px solid transparent;
    padding: 3px 5px;
}
QFileDialog QAbstractItemView::item:hover {
    color: #17212f;
    background: #e4effc;
    border-color: #91b3dc;
}
QFileDialog QAbstractItemView::item:selected,
QFileDialog QAbstractItemView::item:selected:active,
QFileDialog QAbstractItemView::item:selected:!active,
QFileDialog QAbstractItemView::item:selected:hover {
    color: #102033;
    background: #b8d8fa;
    border: 1px solid #2866a8;
}
QFileDialog QAbstractItemView::item:focus { border-color: #174b85; }
QFileDialog QAbstractItemView::item:disabled { color: #4b5563; }
QFileDialog QHeaderView {
    color: #17212f;
    background: #e7edf5;
    border: none;
    border-radius: 0;
    padding: 0;
}
QFileDialog QHeaderView::section {
    color: #17212f;
    background: #e7edf5;
    border: none;
    border-bottom: 1px solid #a7b4c4;
    padding: 6px;
    min-height: 18px;
}
QFileDialog QLineEdit, QFileDialog QComboBox, QFileDialog QSpinBox {
    color: #17212f;
    background: #ffffff;
    border: 1px solid #94a3b8;
    border-radius: 4px;
    padding: 6px 8px;
    selection-background-color: #b8d8fa;
    selection-color: #102033;
}
QFileDialog QComboBox QAbstractItemView {
    color: #17212f;
    background: #ffffff;
    selection-background-color: #b8d8fa;
    selection-color: #102033;
}
QFileDialog QPushButton, QFileDialog QToolButton {
    color: #17212f;
    background: #e8eef6;
    border: 1px solid #94a3b8;
    border-radius: 4px;
    padding: 6px 10px;
}
QFileDialog QPushButton:hover, QFileDialog QToolButton:hover {
    color: #102033;
    background: #d7e7fb;
    border-color: #2866a8;
}
QFileDialog QPushButton:pressed, QFileDialog QToolButton:pressed,
QFileDialog QPushButton:checked, QFileDialog QToolButton:checked {
    color: #102033;
    background: #b8d8fa;
    border-color: #2866a8;
}
QFileDialog QPushButton:disabled, QFileDialog QToolButton:disabled {
    color: #4b5563;
    background: #edf1f5;
    border-color: #b8c3d0;
}
QFileDialog QLineEdit:focus, QFileDialog QComboBox:focus,
QFileDialog QPushButton:focus, QFileDialog QToolButton:focus {
    border: 2px solid #2866a8;
}
QFileDialog QScrollBar:vertical, QFileDialog QScrollBar:horizontal {
    background: #edf1f5;
}
QFileDialog QScrollBar::handle:vertical, QFileDialog QScrollBar::handle:horizontal {
    background: #8c9eb4;
    border-radius: 4px;
    min-height: 24px;
    min-width: 24px;
}
QFileDialog QMenu {
    color: #17212f;
    background: #ffffff;
    border: 1px solid #94a3b8;
}
QFileDialog QMenu::item:selected {
    color: #102033;
    background: #b8d8fa;
}
"""
    _PALETTES = {
        "Studio": ("#10161e", "#18222d", "#1e2b38", "#2b3b4a", "#edf3f7", "#96a8b9", "#7de2c3", "#102b26"),
        "Regen": ("#141820", "#1c2430", "#253140", "#36485a", "#ecf2fa", "#9bb0c5", "#85c8ff", "#122a40"),
        "Dark": ("#161719", "#222426", "#2c2f32", "#42464a", "#f2f3f5", "#b0b6be", "#a6d9c9", "#19332a"),
        "Light": ("#f2f4f3", "#ffffff", "#e8efeb", "#cbd8d0", "#1b3028", "#51675c", "#176b51", "#e0f4ea"),
        "Midnight": ("#141424", "#1e1e34", "#2a2945", "#3d3c5d", "#f1efff", "#aaa7c9", "#b8a5ff", "#30234e"),
        "Yellow": ("#1c1913", "#28231a", "#342e22", "#4c4230", "#faf2dc", "#baad90", "#edce78", "#3b3218"),
        "Green": ("#111c18", "#1a2922", "#23392e", "#355240", "#e6f5ec", "#9dbdaa", "#8bd8a9", "#183c26"),
        "Blue": ("#111a25", "#182738", "#21364b", "#304c66", "#e8f3fc", "#9cb6cc", "#81c6f4", "#153852"),
    }

    @staticmethod
    def config_path():
        return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                            "generic_player", "theme.json")

    @staticmethod
    def load_theme():
        """Use the default for invalid preferences without rewriting the file."""
        from generic_player_library import read_bounded_text
        try:
            name = json.loads(read_bounded_text(
                ThemeManager.config_path(), ThemeManager.MAX_CONFIG_BYTES)).get("theme")
            if name in ThemeManager._PALETTES:
                return name
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            pass
        return ThemeManager.DEFAULT_THEME

    @staticmethod
    def save_theme(theme):
        from generic_player_library import atomic_write_text
        try:
            atomic_write_text(ThemeManager.config_path(), json.dumps({"theme": theme}))
        except OSError:
            pass

    @staticmethod
    def themes():
        return list(ThemeManager._PALETTES)

    @staticmethod
    def palette(theme):
        """Return a caller-owned dictionary, sharing only cached color strings."""
        colors = ThemeManager._PALETTES.get(theme, ThemeManager._PALETTES[ThemeManager.DEFAULT_THEME])
        # Row painting asks for these colors frequently. Cache only the eight
        # small derivations; callers keep their own mutable dictionary.
        return ThemeManager._derived_palette(colors, theme == "Light").copy()

    @staticmethod
    @lru_cache(maxsize=8)
    def _derived_palette(colors, light):
        palette = dict(zip(("background", "surface", "raised", "border", "text", "muted", "accent", "active"), colors))

        def blend(first, second, amount):
            start = tuple(int(first[index:index + 2], 16) for index in (1, 3, 5))
            end = tuple(int(second[index:index + 2], 16) for index in (1, 3, 5))
            return "#" + "".join(f"{round(a + (b - a) * amount):02x}" for a, b in zip(start, end))

        # Keep the theme available to non-GUI helpers: deriving the elevation
        # colors uses plain Python rather than importing Qt just to mix colors.
        palette.update({
            "background_top": blend(palette["background"], palette["surface"], 0.25),
            "surface_top": blend(palette["surface"], palette["text"], 0.035 if not light else 0),
            "raised_top": blend(palette["raised"], "#ffffff", 0.045 if not light else 0.50),
            "highlight": blend(palette["border"], palette["accent"], 0.24 if not light else 0.12),
            "accent_hover": blend(palette["accent"], "#ffffff" if not light else "#000000", 0.12),
            "accent_pressed": blend(palette["accent"], "#000000", 0.12),
            "on_accent": palette["background"] if not light else "#ffffff",
            "shadow": "#03080e" if not light else "#294238",
            "accent_glow": palette["accent"],
        })
        return palette

    @staticmethod
    def qss(theme):
        p = ThemeManager.palette(theme)
        return """
QMainWindow, QWidget#central {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %(background_top)s, stop:1 %(background)s);
    color: %(text)s;
}
QWidget { color: %(text)s; font-family: 'Inter', 'Noto Sans', 'DejaVu Sans', sans-serif; font-size: 12px; }
QLabel { background: transparent; }
QLabel#brand { font-size: 24px; font-weight: 700; letter-spacing: -0.5px; }
QLabel#brand_mark { color: %(accent)s; background: %(active)s; border: 1px solid %(highlight)s; border-radius: 13px; padding: 6px; font-size: 22px; font-weight: 700; }
QLabel#subtitle, QLabel#muted, QLabel#section_hint, QLabel#eyebrow { color: %(muted)s; }
QLabel#subtitle { font-size: 11px; }
QLabel#eyebrow { font-size: 10px; letter-spacing: 1.5px; font-weight: 600; }
QLabel#queue_count { color: %(muted)s; background: %(raised)s; border: 1px solid %(border)s; border-radius: 9px; padding: 4px 8px; font-size: 10px; }
QLabel#section_title { font-size: 18px; font-weight: 600; }
QLabel#track_title { font-size: 27px; font-weight: 700; letter-spacing: -0.3px; }
QLabel#track_artist { font-size: 13px; color: %(muted)s; }
QLabel#status_badge, QLabel#mode_chip { color: %(accent)s; background: %(active)s; border: 1px solid %(highlight)s; border-radius: 11px; padding: 5px 12px; font-size: 10px; font-weight: 600; }
QLabel#empty_queue { color: %(muted)s; background: %(background)s; border: 1px dashed %(border)s; border-radius: 12px; padding: 24px 14px; }
QFrame#card, QWidget#queue_panel, QFrame#transport {
    background: qlineargradient(x1:0, y1:0, x2:0.8, y2:1, stop:0 %(surface_top)s, stop:0.65 %(surface)s, stop:1 %(surface)s);
    border: 1px solid %(border)s;
    border-top-color: %(highlight)s;
    border-radius: 18px;
}
QFrame#transport {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %(raised_top)s, stop:0.45 %(surface_top)s, stop:1 %(surface)s);
}
QFrame#visualizer_card {
    background: qlineargradient(x1:0, y1:0, x2:0.7, y2:1, stop:0 %(background_top)s, stop:1 %(background)s);
    border: 1px solid %(border)s;
    border-radius: 16px;
}
QFrame#hero_header, QWidget#hero_header { background: transparent; border: none; }
QFrame#transport_group { background: %(background_top)s; border: 1px solid %(border)s; border-radius: 12px; }
QPushButton, QToolButton {
    color: %(text)s;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 %(raised_top)s, stop:1 %(raised)s);
    border: 1px solid %(border)s;
    border-top-color: %(highlight)s;
    border-radius: 9px;
    padding: 8px 12px;
    font-weight: 500;
}
QPushButton:hover, QToolButton:hover { border-color: %(accent)s; background: %(active)s; }
QPushButton:pressed, QToolButton:pressed { background: %(background)s; border-color: %(accent)s; }
QPushButton:checked, QToolButton:checked { color: %(accent)s; border-color: %(accent)s; background: %(active)s; }
QPushButton:checked:hover, QToolButton:checked:hover { background: %(raised)s; border-color: %(accent_hover)s; }
QPushButton:focus, QToolButton:focus { border: 2px solid %(accent)s; padding: 7px 11px; }
QPushButton#primary, QPushButton#play_button {
    background: qlineargradient(x1:0, y1:0, x2:0.3, y2:1, stop:0 %(accent_hover)s, stop:1 %(accent)s);
    color: %(on_accent)s;
    border: 1px solid %(accent_hover)s;
    font-weight: 700;
}
QPushButton#play_button { border-radius: 22px; min-width: 82px; min-height: 22px; padding: 10px 20px; font-size: 14px; }
QPushButton#primary:hover, QPushButton#play_button:hover { background: %(accent_hover)s; border-color: %(text)s; }
QPushButton#primary:pressed, QPushButton#play_button:pressed { background: %(accent_pressed)s; border-color: %(accent)s; }
QPushButton#primary:focus { border: 2px solid %(text)s; padding: 7px 11px; }
QPushButton#play_button:focus { border: 2px solid %(text)s; padding: 9px 19px; }
QPushButton#transport_icon { min-width: 20px; min-height: 20px; border-radius: 11px; padding: 8px; }
QPushButton#transport_icon:focus { padding: 7px; }
QPushButton:disabled, QToolButton:disabled, QPushButton#primary:disabled, QPushButton#play_button:disabled {
    color: %(muted)s; background: %(surface)s; border-color: %(border)s;
}
QLineEdit {
    background: %(background)s; border: 1px solid %(border)s; border-radius: 10px;
    padding: 10px 12px; selection-background-color: %(accent)s; selection-color: %(on_accent)s;
}
QLineEdit:hover { border-color: %(highlight)s; }
QLineEdit:focus { border: 2px solid %(accent)s; padding: 9px 11px; }
QComboBox { background: %(raised)s; border: 1px solid %(border)s; border-radius: 8px; padding: 6px 12px; min-height: 18px; }
QComboBox:hover { border-color: %(highlight)s; }
QComboBox:focus { border: 2px solid %(accent)s; padding: 5px 11px; }
QComboBox::drop-down { width: 20px; border: none; }
QComboBox QAbstractItemView { background: %(surface)s; color: %(text)s; border: 1px solid %(highlight)s; padding: 4px; selection-background-color: %(active)s; selection-color: %(accent)s; outline: none; }
QListWidget { background: transparent; border: 1px solid transparent; border-radius: 10px; outline: none; padding: 2px; }
QListWidget:focus { border-color: %(accent)s; }
QListWidget::item { border: 1px solid transparent; border-radius: 9px; padding: 0; }
QListWidget::item:hover { background: %(raised)s; border-color: %(border)s; }
QListWidget::item:selected, QListWidget::item:selected:hover { background: %(active)s; color: %(text)s; border-color: %(highlight)s; }
QTabWidget::pane { border: none; background: transparent; }
QSlider { background: transparent; min-height: 22px; }
QSlider::groove:horizontal { border: none; height: 5px; background: %(border)s; border-radius: 2px; }
QSlider::add-page:horizontal { background: %(border)s; border-radius: 2px; }
QSlider::sub-page:horizontal { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 %(accent_pressed)s, stop:1 %(accent)s); border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; height: 12px; margin: -5px 0; border: 2px solid %(surface)s; border-radius: 7px; background: %(accent)s; }
QSlider::handle:horizontal:hover { background: %(accent_hover)s; border-color: %(accent_hover)s; }
QSlider::handle:horizontal:pressed, QSlider::handle:horizontal:focus { background: %(text)s; border-color: %(accent)s; }
QSlider::sub-page:horizontal:disabled, QSlider::handle:horizontal:disabled { background: %(muted)s; }
QSlider#timeline::groove:horizontal, QSlider#timeline::add-page:horizontal, QSlider#timeline::sub-page:horizontal { height: 6px; border-radius: 3px; }
QSlider#timeline::handle:horizontal { width: 13px; height: 13px; margin: -5px 0; border-radius: 8px; }
QMenuBar { background: %(background)s; padding: 3px 8px; }
QMenuBar::item { padding: 5px 10px; background: transparent; border-radius: 5px; }
QMenuBar::item:selected, QMenu::item:selected { background: %(active)s; color: %(accent)s; }
QMenu { background: %(surface)s; border: 1px solid %(highlight)s; border-radius: 9px; padding: 6px; }
QMenu::item { padding: 8px 24px; border-radius: 5px; }
QMenu::item:disabled { color: %(muted)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 6px; }
QStatusBar { background: %(background)s; color: %(muted)s; font-size: 10px; }
QStatusBar::item { border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 6px 0; }
QScrollBar::handle:vertical { background: %(border)s; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: %(highlight)s; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QSplitter::handle { background: transparent; width: 14px; }
QSplitter::handle:hover { background: %(active)s; border-radius: 5px; margin: 18px 5px; }
QDockWidget { color: %(text)s; }
QDockWidget::title { background: %(surface)s; padding: 10px; }
QTextBrowser, QTextEdit { background: %(surface)s; border: 1px solid %(border)s; border-radius: 12px; padding: 14px; selection-background-color: %(accent)s; selection-color: %(on_accent)s; }
QTextBrowser:focus, QTextEdit:focus { border-color: %(accent)s; }
QToolTip { background: %(raised)s; color: %(text)s; border: 1px solid %(highlight)s; border-radius: 5px; padding: 7px 9px; }
QDialog, QMessageBox, QFileDialog { background: %(background)s; }
""" % p + ThemeManager._FILE_DIALOG_QSS
