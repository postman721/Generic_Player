#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
theme.py - Generic Player themes (QSS) + persistence

"""

from __future__ import annotations

import json
import os
from typing import List


class ThemeManager:
    """Lightweight theme system (QSS) with persistence."""

    DEFAULT_THEME = "Regen"

    @staticmethod
    def config_path() -> str:
        base = (
            os.environ.get("XDG_CONFIG_HOME")
            or os.path.join(os.path.expanduser("~"), ".config")
        )
        return os.path.join(base, "generic_player", "theme.json")

    @staticmethod
    def load_theme() -> str:
        try:
            path = ThemeManager.config_path()
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                theme = str(data.get("theme") or "").strip()
                if theme:
                    return theme
        except Exception:
            pass
        return ThemeManager.DEFAULT_THEME

    @staticmethod
    def save_theme(theme: str) -> None:
        try:
            path = ThemeManager.config_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"theme": str(theme)}, f, indent=2)
        except Exception:
            pass

    @staticmethod
    def themes() -> List[str]:
        # Keep list stable so saved themes remain selectable.
        return ["Regen", "Dark", "Light", "Midnight", "Yellow", "Green", "Blue"]

    @staticmethod
    def qss(theme: str) -> str:
        """Return QSS for a given theme name (case-insensitive)."""
        key = (theme or "").strip().lower()
        mapping = {
            "regen": ThemeManager._REGEN_QSS,
            "dark": ThemeManager._DARK_QSS,
            "light": ThemeManager._LIGHT_QSS,
            "midnight": ThemeManager._MIDNIGHT_QSS,
            "yellow": ThemeManager._YELLOW_QSS,
            "green": ThemeManager._GREEN_QSS,
            "blue": ThemeManager._BLUE_QSS,
        }
        # Fallback to the industrial glossy dark theme.
        return mapping.get(key, ThemeManager._REGEN_QSS)

    # ---------------- Regen (Industrial glossy dark) ----------------
    _REGEN_QSS = r"""
/* ==========================================================
   REGEN (Industrial glossy dark)
   - Dark metal surfaces, subtle highlights, blue accents
   - Works well with icons and video area
   ========================================================== */

/* Global defaults */
* {
    background-color: #0e1116;
    color: #e7eef7;
    font-family: 'Segoe UI', 'Inter', 'Noto Sans', sans-serif;
    font-size: 12px;
}

/* Main window & containers */
QMainWindow, QWidget {
    background-color: #636363;
    color: white;
}

/* Sub-panels (tabs container) */
QTabWidget::pane {
    border: 1px solid #2a323c;
    border-radius: 12px;
    background-color: #10151c;
}

/* Tabs */
QTabBar::tab {
    background-color: #121923;
    color: #cfd9e5;
    border: 1px solid #2a323c;
    border-bottom: none;
    padding: 9px 14px;
    margin-right: 6px;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
}
QTabBar::tab:hover {
    background-color: #162131;
}
QTabBar::tab:selected {
    background-color: #141d29;
    color: #f0f6ff;
    border: 1px solid #3a4552;
}

/* Buttons (glossy / beveled) */
QPushButton {
    background-color: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1c2735,
        stop:0.45 #141c26,
        stop:0.55 #111822,
        stop:1 #0f151e
    );
    color: #e7eef7;
    border: 1px solid #3a4552;
    border-radius: 10px;
    padding: 9px 14px;
    font-weight: 600;
}

QPushButton:hover {
    border: 1px solid #4a5a6c;
    background-color: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #263649,
        stop:0.45 #192434,
        stop:0.55 #141f2e,
        stop:1 #101823
    );
}

QPushButton:pressed {
    background-color: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #101722,
        stop:1 #0c1119
    );
    border: 1px solid #2e3946;
    padding-top: 10px; /* press illusion */
}

QPushButton:disabled {
    background-color: #121821;
    color: #6f7c8a;
    border: 1px solid #222a34;
}

/* Checkable buttons (Mute / Shuffle / Repeat / Lyrics) */
QPushButton:checked {
    border: 1px solid #2b6aa6;
    background-color: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1a2f46,
        stop:1 #0f1d2c
    );
}

/* Labels */
QLabel {
    background: transparent;
    color: #dfe7f1;
}
QLabel#clock_label {
    color: #d7f0ff;
    font-weight: 800;
    letter-spacing: 1px;
}

/* Slider */
QSlider::groove:horizontal {
    border: 1px solid #2a323c;
    height: 8px;
    background: #10151c;
    border-radius: 4px;
}
QSlider::handle:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2b6aa6,
        stop:1 #173754
    );
    border: 1px solid #5aa6e6;
    width: 16px;
    margin: -6px 0;
    border-radius: 8px;
}
QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #2b6aa6,
        stop:1 #173754
    );
    border-radius: 4px;
}

/* Playlist */
QListWidget {
    background-color: #0f141b;
    border: 1px solid #2a323c;
    border-radius: 12px;
    padding: 6px;
}
QListWidget::item {
    background-color: #101722;
    border: 1px solid transparent;
    padding: 10px;
    margin: 4px;
    border-radius: 10px;
    color: #dfe7f1;
}
QListWidget::item:hover {
    background-color: #142033;
    border: 1px solid #2b6aa6;
}
QListWidget::item:selected {
    background-color: #182842;
    border: 1px solid #5aa6e6;
    color: #f2f7ff;
}

/* Inputs */
QLineEdit, QComboBox {
    background-color: #0f141b;
    border: 1px solid #2a323c;
    border-radius: 10px;
    padding: 8px 10px;
    color: #e7eef7;
}
QLineEdit:focus, QComboBox:focus {
    border: 1px solid #5aa6e6;
}
QComboBox::drop-down {
    border-left: 1px solid #2a323c;
    width: 26px;
}
QComboBox QAbstractItemView {
    background-color: #0f141b;
    border: 1px solid #2a323c;
    selection-background-color: #182842;
    selection-color: #f2f7ff;
}

/* Menu */
QMenuBar {
    background-color: #0f141b;
    color: #e7eef7;
    border-bottom: 1px solid #2a323c;
}
QMenuBar::item {
    background: transparent;
    padding: 6px 10px;
    border-radius: 8px;
}
QMenuBar::item:selected {
    background-color: #142033;
}
QMenu {
    background-color: #0f141b;
    color: #e7eef7;
    border: 1px solid #2a323c;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    padding: 8px 14px;
    border-radius: 8px;
}
QMenu::item:selected {
    background-color: #182842;
}

/* Status bar */
QStatusBar {
    background-color: #0f141b;
    color: #b9c6d6;
    border-top: 1px solid #2a323c;
}

/* Video widget */
#video_widget {
    background-color: #0b0e12;
    border: 1px solid #2a323c;
    border-radius: 14px;
}
"""

    # ---------------- Dark ----------------
    _DARK_QSS = r"""
* {
    background-color: #0f1720;
    color: #e6edf3;
    font-family: 'Segoe UI', 'Inter', 'Noto Sans', sans-serif;
    font-size: 12px;
}

QLabel { color: #b7c2cc; font-size: 11px; }

QPushButton {
    background-color: #1b2631;
    color: #e6edf3;
    border: 1px solid #2a3a48;
    border-radius: 8px;
    padding: 9px 14px;
    font-weight: 600;
}
QPushButton:hover { background-color: #223243; }
QPushButton:pressed { background-color: #15202a; }
QPushButton:disabled {
    background-color: #121b23;
    color: #7f8c97;
}
"""

    # ---------------- Light (baseline you provided) ----------------
    _LIGHT_QSS = r"""
* {
    background-color: #f7f9fb;
    color: #0c1520;
    font-family: 'Segoe UI', 'Inter', 'Noto Sans', sans-serif;
    font-size: 12px;
}

QPushButton {
    background-color: #f0f3f6;
    color: #0c1520;
    border: 1px solid #c8d1da;
    border-radius: 8px;
    padding: 9px 14px;
    font-weight: 600;
}
"""

    # ---------------- Midnight ----------------
    _MIDNIGHT_QSS = r"""
* {
    background-color: #070a0f;
    color: #e6edf3;
    font-family: 'Segoe UI', 'Inter', 'Noto Sans', sans-serif;
    font-size: 12px;
}

QLabel { color: #a9b7c6; font-size: 11px; }

QPushButton {
    background-color: #0f1620;
    color: #e6edf3;
    border: 1px solid #1e2a3a;
    border-radius: 8px;
    padding: 9px 14px;
    font-weight: 600;
}
"""

    # ---------------- Yellow ----------------
    _YELLOW_QSS = r"""
QWidget {
    background-color: #fff7cc;
    color: #1a1a1a;
    font-size: 13px;
}
QPushButton {
    background-color: #ffd84d;
    color: #1a1a1a;
    border: 1px solid #c9a300;
    border-radius: 6px;
    font-weight: 600;
}
"""

    # ---------------- Green ----------------
    _GREEN_QSS = r"""
QWidget {
    background-color: #eaffea;
    color: #102010;
    font-size: 13px;
}
QPushButton {
    background-color: #22c55e;
    color: #ffffff;
    border: 1px solid #16803c;
    border-radius: 6px;
    font-weight: 600;
}
"""

    # ---------------- Blue ----------------
    _BLUE_QSS = r"""
QWidget {
    background-color: #eaf3ff;
    color: #0b1524;
    font-size: 13px;
}
QPushButton {
    background-color: #3b82f6;
    color: #ffffff;
    border: 1px solid #1f5fc2;
    border-radius: 6px;
    font-weight: 600;
}
"""
