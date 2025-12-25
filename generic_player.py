#!/usr/bin/env python3
"""
Generic Player 1.0  (PyQt6 / PyQt5 compatible)
------------------------------------------------
Lyrics: integrated via external generic_player_lyrics module (show/hide only; no save/overlay UI).
Mpris: integated via generic_player_mpris module. New dependencies: python3-pydbus playerctl
License: GPL v2
Author: JJ Posti <techtimejourney.net>
A spiritual successor to my now-obsolete Albix Player, continuing its legacy with a cleaner, more maintainable codebase.
"""

import sys
sys.dont_write_bytecode = True
import os
import json
import random
import time
from os.path import basename, splitext
from theme import *
import generic_player_mpris
import generic_player_lyrics

# ---------------- PyQt6 first, fallback to PyQt5 ----------------
USING_QT6 = False
try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    from PyQt6.QtCore import Qt, QUrl, QSize
    from PyQt6.QtGui import QIcon, QAction, QKeySequence
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
        QListWidget, QFileDialog, QSlider, QAbstractItemView, QMessageBox, QLabel,
        QTabWidget, QLineEdit, QStatusBar, QMenuBar, QComboBox
    )
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    USING_QT6 = True
    print("Using PyQt6")
except Exception as e:
    print("PyQt6 import failed, falling back to PyQt5:", e)
    from PyQt5 import QtCore, QtGui, QtWidgets
    from PyQt5.QtCore import Qt, QUrl, QSize
    from PyQt5.QtGui import QIcon, QKeySequence
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
        QListWidget, QFileDialog, QSlider, QAbstractItemView, QMessageBox, QLabel,
        QTabWidget, QLineEdit, QStatusBar, QMenuBar, QComboBox, QAction
    )
    from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
    from PyQt5.QtMultimediaWidgets import QVideoWidget
    USING_QT6 = False
    print("Using PyQt5")

# ---------------- Cross-version helpers ----------------
def align_center():
    return Qt.AlignmentFlag.AlignCenter if USING_QT6 else Qt.AlignCenter

def key(name: str):
    return getattr(Qt.Key, name) if USING_QT6 else getattr(Qt, name)

def file_dialog_options(use_native: bool = False):
    if USING_QT6:
        opt = QtWidgets.QFileDialog.Option(0)
        if not use_native:
            opt |= QtWidgets.QFileDialog.Option.DontUseNativeDialog
        return opt
    else:
        opt = QFileDialog.Options()
        if not use_native:
            opt |= QFileDialog.DontUseNativeDialog
        return opt

def _is_wayland() -> bool:
    try:
        plat = QtWidgets.QApplication.platformName()
    except Exception:
        plat = ""
    return (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland") or ("wayland" in plat.lower())

def _safe_bring_to_front(w):
    """Show/raise without Wayland warnings."""
    if not w:
        return
    try:
        w.show()
        if not _is_wayland():
            if hasattr(w, "raise_"):
                w.raise_()
            if hasattr(w, "activateWindow"):
                w.activateWindow()
    except Exception:
        pass

# ---------------- Animated button (simple hover) ----------------
class AnimatedButton(QtWidgets.QPushButton):
    """Button that can optionally override hover styling.

    By default, the active ThemeManager QSS controls all button states.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._default_qss = ""
        self._hover_qss = ""

    def apply_button_qss(self, default_qss: str = "", hover_qss: str = ""):
        self._default_qss = default_qss or ""
        self._hover_qss = hover_qss or ""
        self.setStyleSheet(self._default_qss or "")

    def enterEvent(self, e):
        if self._hover_qss:
            self.setStyleSheet(self._hover_qss)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.setStyleSheet(self._default_qss or "")
        super().leaveEvent(e)


# ---------------- Main Window ----------------
class MainWindow(QMainWindow):
    SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".wmv"}
    SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".ogg", ".flac", ".wav"}

    def __init__(self):
        super().__init__()

        # Window
        self.setWindowTitle("Generic Player")
        self.setGeometry(100, 100, 1000, 700)
        self.setAcceptDrops(True)

        # Last played state 
        self._last_playing = False


        # Last playing flag used when backend state lags (media keys / MPRIS)
        self._mpris_last_playing = False

        # UI throttles (reduce label/slider churn)
        self._last_ui_second = -1
        self._last_slider_update_ms = -999999
        # Playlist & state
        self.playlist = []
        self.current_song_index = -1
        self.shuffle_mode = False
        self.repeat_mode = False
        self.current_media_type = 'audio'
        self._lyrics_visible = False

        # Track-end watchdog
        self._duration_ms = 0
        self._end_guard = False

        # Debounce guard for play/pause toggles (prevents double-toggle from key+MPRIS)
        self._toggle_guard_until = 0.0

        # ---- MPRIS / playerctl support ----
        self.mpris = None
        try:
            self.mpris = generic_player_mpris.GenericPlayerMPRIS(self)
            # Drive GLib/DBus from Qt without blocking
            self._mpris_timer = QtCore.QTimer(self)
            self._mpris_timer.timeout.connect(self.mpris.poll)
            self._mpris_timer.start(80)
        except Exception as e:
            self.mpris = None
            print("MPRIS disabled:", e)

        # Multimedia setup
        self.player = QMediaPlayer(self)
        if USING_QT6:
            self.audio_out = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_out)

        # Prefer Qt6 signal name when available; fall back to Qt5 signal.
        # Use getattr() so missing methods can never raise AttributeError during init.
        if hasattr(self.player, "playbackStateChanged"):
            cb = getattr(self, "_on_playback_state_changed", None)
            if callable(cb):
                self.player.playbackStateChanged.connect(cb)
        elif hasattr(self.player, "stateChanged"):
            cb = getattr(self, "_on_state_changed", None)
            if callable(cb):
                self.player.stateChanged.connect(cb)

        self.player.positionChanged.connect(self.update_slider)
        self.player.durationChanged.connect(self.set_duration)
        self.player.mediaStatusChanged.connect(self.handle_media_status)

        # Error signal differs between Qt versions
        if hasattr(self.player, "errorOccurred"):
            self.player.errorOccurred.connect(self.handle_error)
        elif hasattr(self.player, "error"):
            self.player.error.connect(self.handle_error)

        # Theme
        self._current_theme = ThemeManager.load_theme()

        # UI
        self._build_ui()
        self.apply_theme(self._current_theme)


        # Fullscreen UI state
        self._fs_saved_tab_max_h = None
        self._fs_saved_tabbar_visible = True

        # ---- Lyrics integration (show/hide only) ----
        try:
            self.lyrics = generic_player_lyrics.GenericPlayerLyrics(self, video_widget=self.video_widget)
        except Exception as e:
            self.lyrics = None
            QMessageBox.warning(self, "Lyrics",
                                f"Failed to initialize Generic Player Lyrics module:\n{e}")

        # Initialize volume to slider value
        self.change_volume(self.volume_slider.value())

        # Global key handling (Alt+P, Space, Media keys)
        try:
            QtWidgets.QApplication.instance().installEventFilter(self)
        except Exception:
            pass

        # Application-level shortcut: Alt+P (toggle)
        try:
            self._toggle_action = QAction(self)
            self._toggle_action.setShortcut(QKeySequence("Alt+P"))

            # Make sure Alt+P works app-wide and fires ONLY once
            self._toggle_action.setShortcutContext(
                Qt.ShortcutContext.ApplicationShortcut
                if USING_QT6 else Qt.ApplicationShortcut
            )
            self._toggle_action.setAutoRepeat(False)
            self._toggle_action.activated.connect(self._toggle_play_pause_shortcut)
        except Exception:
            pass

    # ---------------- Drag & Drop ----------------
    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()]
            self._process_dropped_files(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def _process_dropped_files(self, files):
        for file_path in files:
            ext = splitext(file_path)[1].lower()
            if ext in self.SUPPORTED_VIDEO_EXTENSIONS:
                mtype = "video"
            elif ext in self.SUPPORTED_AUDIO_EXTENSIONS:
                mtype = "audio"
            else:
                continue
            if not os.path.exists(file_path):
                continue
            if any(it.get("path") == file_path for it in self.playlist):
                continue
            meta = self._build_media_meta(file_path, mtype)
            self.playlist.append({"path": file_path, "type": mtype, **meta})
            self.playlist_widget.addItem(meta.get("display") or basename(file_path))
        self._update_controls_enabled()

# Toggle guard
    def _toggle_guard_active(self, window_ms: int = 220) -> bool:
        """
        Returns True if we should IGNORE a toggle (because another toggle just happened).
        Prevents double-toggle caused by multiple sources firing at once.
        """
        now = time.monotonic()
        try:
            until = float(getattr(self, "_toggle_guard_until", 0.0))
        except Exception:
            until = 0.0

        if now < until:
            return True

        self._toggle_guard_until = now + (max(0, int(window_ms)) / 1000.0)
        return False
        
    def _mpris_force_actual_status(self):
        """
        Force MPRIS PlaybackStatus to match the *current* Qt player state.
        This fixes reversed OSD text on some Wayland compositors.
        """
        m = getattr(self, "mpris", None)
        if not m:
            return

        status = "Stopped"
        try:
            if USING_QT6:
                from PyQt6.QtMultimedia import QMediaPlayer as QMP
                st = self.player.playbackState()
                if st == QMP.PlaybackState.PlayingState:
                    status = "Playing"
                elif st == QMP.PlaybackState.PausedState:
                    status = "Paused"
                else:
                    status = "Stopped"
            else:
                st = self.player.state()
                if st == QMediaPlayer.State.PlayingState:
                    status = "Playing"
                elif st == QMediaPlayer.State.PausedState:
                    status = "Paused"
                else:
                    status = "Stopped"
        except Exception:
            # Fallback: use your existing helper if Qt query fails
            try:
                status = "Playing" if self.player_is_playing() else "Paused"
            except Exception:
                status = "Stopped"

        try:
            m.force_playback_status(status, timeout_ms=650)
        except Exception:
            pass
        
    def _mpris_force_status_immediate(self, status: str, timeout_ms: int = 650):
        """Force an immediate MPRIS PlaybackStatus (for compositor OSD).

        This is used for media keys on Wayland so the OSD text matches the
        action we're about to take (Playing vs Paused).
        """
        m = getattr(self, "mpris", None)
        if not m:
            return
        try:
            status = (status or "").strip().title()
            if status not in ("Playing", "Paused", "Stopped"):
                return
            m.force_playback_status(status, timeout_ms=int(timeout_ms))
        except Exception:
            pass


    def _toggle_play_pause_shortcut(self):
        """Unified play/pause toggle for Alt+P / Space / media keys.

        Goal:
        - single press toggles reliably (no double-toggle from multiple sources)
        - Wayland compositor OSD shows the *next* state correctly for media keys

        Implementation:
        - Determine whether we're effectively playing *right now* using a robust
          heuristic (backend state + recent position movement).
        - Pre-force the intended next MPRIS PlaybackStatus so the OSD text matches.
        - Toggle playback.
        - Re-sync MPRIS to the actual backend state shortly after it settles.
        """
        try:
            # Robust "are we playing right now?"
            try:
                playing_now = bool(self._is_effectively_playing())
            except Exception:
                playing_now = bool(getattr(self, "_last_playing", False))

            intended = "Paused" if playing_now else "Playing"

            # Make sure the compositor OSD sees the right text for media keys.
            self._mpris_force_status_immediate(intended)

            # Help play_pause_song() avoid stale backend reads at keypress time.
            self._last_playing = playing_now

            # Toggle playback (guard is handled inside play_pause_song()).
            self.play_pause_song(prefer_last_state=True)

            # Then re-sync to the real backend state once it settles.
            QtCore.QTimer.singleShot(120, self._mpris_force_actual_status)
        except Exception:
            # Fallback: attempt plain toggle
            try:
                self.play_pause_song()
            except Exception:
                pass

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # Video widget
        self.video_widget = QVideoWidget(self)
        self.video_widget.setObjectName("video_widget")
        self.video_widget.setMinimumSize(640, 360)
        self.video_widget.hide()
        main_layout.addWidget(self.video_widget)

        # Connect player to video widget
        self.player.setVideoOutput(self.video_widget)

        # Tabs
        self.tab_widget = QTabWidget(self)
        self.tab_widget.setTabPosition(QTabWidget.TabPosition.North if USING_QT6 else QTabWidget.North)
        self.tab_widget.setTabShape(QTabWidget.TabShape.Rounded if USING_QT6 else QTabWidget.Rounded)

        # Single tab UI: Radio tab removed.
        # Radio stations + custom station row are integrated into Local Files tab.
        self.music_tab = QWidget(self)

        self.tab_widget.addTab(self.music_tab, "Playback")

        self._setup_music_tab()

        main_layout.addWidget(self.tab_widget)

        # Slider + time
        s_layout = QHBoxLayout()
        s_layout.setAlignment(align_center())

        self.current_time_label = QLabel("00:00:00")
        self.current_time_label.setObjectName("elapsed_clock")
        self.current_time_label.setStyleSheet("font-family: monospace; font-size: 14px;")
        s_layout.addWidget(self.current_time_label)

        self.playback_slider = QSlider(Qt.Orientation.Horizontal if USING_QT6 else Qt.Horizontal)
        self.playback_slider.setRange(0, 0)
        self.playback_slider.sliderMoved.connect(self.seek_position)
        self.playback_slider.setEnabled(False)
        s_layout.addWidget(self.playback_slider)

        self.total_time_label = QLabel("00:00:00")
        self.total_time_label.setObjectName("total_clock")
        self.total_time_label.setStyleSheet("font-family: monospace; font-size: 14px;")
        s_layout.addWidget(self.total_time_label)

        main_layout.addLayout(s_layout)

        # Volume + mute
        v_layout = QHBoxLayout()
        v_layout.setAlignment(align_center())

        self.mute_button = AnimatedButton("Mute")
        self.mute_button.setCheckable(True)
        self.mute_button.clicked.connect(self.toggle_mute)
        v_layout.addWidget(self.mute_button)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal if USING_QT6 else Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setFixedWidth(150)
        self.volume_slider.valueChanged.connect(self.change_volume)
        v_layout.addWidget(self.volume_slider)

        # Theme switcher
        self.theme_combo = QComboBox(self)
        self.theme_combo.addItems(ThemeManager.themes())
        try:
            idx = self.theme_combo.findText(getattr(self, "_current_theme", ThemeManager.DEFAULT_THEME))
            if idx >= 0:
                self.theme_combo.setCurrentIndex(idx)
        except Exception:
            pass
        self.theme_combo.currentTextChanged.connect(self.apply_theme)
        v_layout.addWidget(self.theme_combo)

        main_layout.addLayout(v_layout)

        # Status bar
        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)

        # File menu 
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        save_action = QAction("Save Playlist", self)
        save_action.triggered.connect(self.save_playlist)
        file_menu.addAction(save_action)

        load_action = QAction("Load Playlist", self)
        load_action.triggered.connect(self.load_playlist)
        file_menu.addAction(load_action)

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Theme menu
        theme_menu = menubar.addMenu("Theme")
        self._theme_actions = {}
        for t in ThemeManager.themes():
            act = QAction(t, self)
            act.setCheckable(True)
            act.triggered.connect(lambda checked=False, name=t: self.apply_theme(name))
            theme_menu.addAction(act)
            self._theme_actions[t] = act

        try:
            cur = getattr(self, "_current_theme", ThemeManager.DEFAULT_THEME)
            if cur in self._theme_actions:
                self._theme_actions[cur].setChecked(True)
        except Exception:
            pass

        self._update_controls_enabled()

    def _setup_music_tab(self):
        layout = QVBoxLayout(self.music_tab)

        # Row 1: transport
        row1 = QHBoxLayout()
        row1.setSpacing(10)

        self.prev_button = AnimatedButton("Prev")
        self.prev_button.setObjectName("prev_button")
        self.prev_button.clicked.connect(self.prev_song)
        self.prev_button.setEnabled(False)
        row1.addWidget(self.prev_button)

        self.play_button = AnimatedButton("Play")
        self.play_button.setObjectName("play_button")
        self.play_button.clicked.connect(self.play_pause_song)
        self.play_button.setEnabled(False)
        row1.addWidget(self.play_button)

        self.next_button = AnimatedButton("Next")
        self.next_button.setObjectName("next_button")
        self.next_button.clicked.connect(self.next_song)
        self.next_button.setEnabled(False)
        row1.addWidget(self.next_button)

        self.stop_button = AnimatedButton("Stop")
        self.stop_button.clicked.connect(self.stop_song)
        self.stop_button.setEnabled(False)
        row1.addWidget(self.stop_button)

        layout.addLayout(row1)

        # Row 2: library & modes
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        self.add_button = AnimatedButton("Add")
        self.add_button.clicked.connect(self.add_songs)
        row2.addWidget(self.add_button)

        self.remove_button = AnimatedButton("Remove")
        self.remove_button.clicked.connect(self.remove_songs)
        self.remove_button.setEnabled(False)
        row2.addWidget(self.remove_button)

        self.shuffle_button = AnimatedButton("Shuffle OFF")
        self.shuffle_button.setCheckable(True)
        self.shuffle_button.clicked.connect(self.toggle_shuffle)
        self.shuffle_button.setEnabled(False)
        row2.addWidget(self.shuffle_button)

        self.repeat_button = AnimatedButton("Repeat OFF")
        self.repeat_button.setCheckable(True)
        self.repeat_button.clicked.connect(self.toggle_repeat)
        self.repeat_button.setEnabled(False)
        row2.addWidget(self.repeat_button)

        self.lyrics_button = AnimatedButton("Lyrics")
        self.lyrics_button.setCheckable(True)
        self.lyrics_button.clicked.connect(self.toggle_lyrics)
        row2.addWidget(self.lyrics_button)

        layout.addLayout(row2)

        # Playlist
        self.playlist_widget = QListWidget(self.music_tab)
        sel_mode = QAbstractItemView.SelectionMode.ExtendedSelection if USING_QT6 else QAbstractItemView.ExtendedSelection
        self.playlist_widget.setSelectionMode(sel_mode)
        self.playlist_widget.itemDoubleClicked.connect(self.play_selected_song)
        self.playlist_widget.setStyleSheet("""
            QListWidget::item {
                padding: 10px;
                font-size: 12px;
            }
        """)
        layout.addWidget(self.playlist_widget)

        # Custom station row (moved here, to the bottom of the Local Files UI)
        row = QHBoxLayout()
        self.custom_station_name = QLineEdit(self.music_tab)
        self.custom_station_name.setPlaceholderText("Stream Name")
        self.custom_station_url = QLineEdit(self.music_tab)
        self.custom_station_url.setPlaceholderText("Stream URL (http/https)")
        add_station = AnimatedButton("Add Stream")
        add_station.clicked.connect(self.add_custom_stream)

        row.addWidget(self.custom_station_name)
        row.addWidget(self.custom_station_url)
        row.addWidget(add_station)
        layout.addLayout(row)

    # ---------------- Theme ----------------
    def apply_theme(self, theme_name: str):
        """Apply a named theme (and persist it)."""
        theme_name = (theme_name or ThemeManager.DEFAULT_THEME).strip()
        if theme_name not in ThemeManager.themes():
            theme_name = ThemeManager.DEFAULT_THEME

        self._current_theme = theme_name
        self.setStyleSheet(ThemeManager.qss(theme_name))

        # Keep UI controls in sync
        try:
            if hasattr(self, "theme_combo") and self.theme_combo is not None:
                idx = self.theme_combo.findText(theme_name)
                if idx >= 0 and self.theme_combo.currentIndex() != idx:
                    self.theme_combo.blockSignals(True)
                    self.theme_combo.setCurrentIndex(idx)
                    self.theme_combo.blockSignals(False)
        except Exception:
            pass

        try:
            if hasattr(self, "_theme_actions"):
                for t, act in self._theme_actions.items():
                    act.setChecked(t == theme_name)
        except Exception:
            pass

        ThemeManager.save_theme(theme_name)
        try:
            self.status_bar.showMessage(f"Theme: {theme_name}")
        except Exception:
            pass

        # ---------------- Menu actions ----------------
    def save_playlist(self):
        if not self.playlist:
            QMessageBox.information(self, "Empty Playlist", "There is no playlist to save.")
            return
        opts = file_dialog_options(False)
        file_name, _ = QFileDialog.getSaveFileName(self, "Save Playlist", "", "JSON Files (*.json)", options=opts)
        if file_name:
            try:
                with open(file_name, 'w', encoding='utf-8') as f:
                    json.dump(self.playlist, f, indent=4)
                QMessageBox.information(self, "Playlist Saved", f"Playlist saved to {file_name}")
            except Exception as e:
                QMessageBox.critical(self, "Error Saving Playlist", str(e))

    def load_playlist(self):
        opts = file_dialog_options(False)
        file_name, _ = QFileDialog.getOpenFileName(self, "Load Playlist", "", "JSON Files (*.*)", options=opts)
        if file_name:
            try:
                with open(file_name, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list) and all('path' in d and 'type' in d for d in data):
                    self.playlist = data
                    self.playlist_widget.clear()
                    for item in self.playlist:
                        # Backfill metadata for older playlists
                        if "display" not in item or "artist" not in item or "title" not in item:
                            meta = self._build_media_meta(item.get("path",""), item.get("type","audio"))
                            item.update(meta)
                        self.playlist_widget.addItem(item.get("display") or basename(item.get('path','')))
                    self._update_controls_enabled()
                    QMessageBox.information(self, "Playlist Loaded", f"Playlist loaded from {file_name}")
                else:
                    QMessageBox.warning(self, "Invalid File", "The selected JSON does not contain a valid playlist.")
            except Exception as e:
                QMessageBox.critical(self, "Error Loading Playlist", str(e))

    # ---------------- Music tab actions ----------------
    def add_songs(self):
        opts = file_dialog_options(False)
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Add Media Files",
            "",
            "Media Files (*.mp3 *.ogg *.flac *.wav *.mp4 *.avi *.mkv *.mov *.wmv);;All Files (*)",
            options=opts
        )
        if not files:
            return
        for file_path in files:
            if any(it["path"] == file_path for it in self.playlist):
                continue
            ext = splitext(file_path)[1].lower()
            if ext in self.SUPPORTED_VIDEO_EXTENSIONS:
                mtype = "video"
            elif ext in self.SUPPORTED_AUDIO_EXTENSIONS:
                mtype = "audio"
            else:
                QMessageBox.warning(self, "Unsupported Format", f"Skipping:\n{basename(file_path)}")
                continue
            if not os.path.exists(file_path):
                QMessageBox.warning(self, "File Not Found", f"The file does not exist:\n{basename(file_path)}")
                continue
            meta = self._build_media_meta(file_path, mtype)
            self.playlist.append({"path": file_path, "type": mtype, **meta})
            self.playlist_widget.addItem(meta.get("display") or basename(file_path))
        self._update_controls_enabled()

    def remove_songs(self):
        selected = self.playlist_widget.selectedItems()
        if not selected:
            return
        for item in selected:
            idx = self.playlist_widget.row(item)
            if 0 <= idx < len(self.playlist):
                self.playlist.pop(idx)
                self.playlist_widget.takeItem(idx)
                if idx == self.current_song_index:
                    self.stop_song()
        if self.current_song_index >= len(self.playlist):
            self.current_song_index = len(self.playlist) - 1
        self._update_controls_enabled()

    def play_selected_song(self, item=None):
        """Play the selected/double-clicked song."""
        try:
            if item is not None:
                self.current_song_index = self.playlist_widget.row(item)
            else:
                self.current_song_index = self.playlist_widget.currentRow()
        except Exception:
            self.current_song_index = self.playlist_widget.currentRow()
        self.play_song()
    # ---------------- Playback control ----------------


    def play_pause_song(self, prefer_last_state: bool = False):
        """Toggle play/pause.

        - If playing: pause (and show OSD 'Paused')
        - If paused: play (and show OSD 'Playing')
        - If stopped/no media: start current/first track (and show OSD 'Playing')

        prefer_last_state=True can be used for media keys on backends where the
        reported state lags at the exact moment of the key press.
        """
        if self._toggle_guard_active():
            return

        # Determine backend state
        playing_backend = False
        paused_backend = False
        stopped_backend = False
        try:
            if USING_QT6:
                from PyQt6.QtMultimedia import QMediaPlayer as _QMP
                st = self.player.playbackState()
                playing_backend = (st == _QMP.PlaybackState.PlayingState)
                paused_backend = (st == _QMP.PlaybackState.PausedState)
                stopped_backend = (st == _QMP.PlaybackState.StoppedState)
            else:
                st = self.player.state()
                playing_backend = (st == QMediaPlayer.State.PlayingState)
                paused_backend = (st == QMediaPlayer.State.PausedState)
                stopped_backend = (st == QMediaPlayer.State.StoppedState)
        except Exception:
            # If we can't query state, assume "not playing" and try to play.
            playing_backend = False
            paused_backend = False
            stopped_backend = True

        playing = playing_backend
        paused = paused_backend
        stopped = stopped_backend

        # Optionally prefer the last state we observed via signals
        if prefer_last_state:
            last_playing = getattr(self, "_mpris_last_playing", None)
            if isinstance(last_playing, bool):
                # If backend is ambiguous or lags, trust the last known flag.
                if (not playing_backend and not paused_backend) or stopped_backend:
                    playing = last_playing
                    paused = not last_playing

        # If currently playing -> pause
        if playing:
            self._mpris_force_status_immediate("Paused")
            try:
                self.player.pause()
            except Exception:
                pass
            QtCore.QTimer.singleShot(140, lambda: self._mpris_notify(playback=True))
            return

        # If currently paused -> play
        if paused:
            self._mpris_force_status_immediate("Playing")
            try:
                self.player.play()
            except Exception:
                pass
            QtCore.QTimer.singleShot(140, lambda: self._mpris_notify(playback=True))
            return

        # If stopped/no media -> load current track (or first track)
        if self.current_song_index == -1 and self.playlist:
            self.current_song_index = 0
            self.playlist_widget.setCurrentRow(0)

        # If nothing to play, do nothing
        if not self.playlist:
            return

        # Start playback from stopped state
        self._mpris_force_status_immediate("Playing")
        self.play_song()
        QtCore.QTimer.singleShot(200, lambda: self._mpris_notify(playback=True))

    def play_song(self):

        if not (0 <= self.current_song_index < len(self.playlist)):
            return
        media_info = self.playlist[self.current_song_index]
        # Ensure we have display metadata (older playlist entries)
        if "display" not in media_info or "title" not in media_info or "artist" not in media_info:
            media_info.update(self._build_media_meta(media_info.get("path",""), media_info.get("type","audio")))
        file_path = media_info["path"]
        mtype = media_info["type"]
        self.current_media_type = mtype

        # Stream URLs are not files on disk.
        if mtype == "stream":
            self.video_widget.hide()
            if USING_QT6:
                self.player.setSource(QUrl(file_path))
            else:
                self.player.setMedia(QMediaContent(QUrl(file_path)))

            # reset end-guard for a new source
            self._end_guard = False
            self.player.play()

            # Notify MPRIS clients
            self._mpris_force_status_immediate("Playing")
            self._mpris_notify(metadata=True)
            QtCore.QTimer.singleShot(140, lambda: self._mpris_notify(playback=True))

            self.playback_slider.setEnabled(True)
            self.stop_button.setEnabled(True)
            self.status_bar.showMessage(f"Streaming: {media_info.get('display') or file_path}")
            self._lyrics_call("clear")
            return

        if not os.path.exists(file_path):
            QMessageBox.warning(self, "File Not Found", f"The file was not found:\n{basename(file_path)}")
            return

        if mtype == "video":
            self.video_widget.show()
        else:
            self.video_widget.hide()

        if USING_QT6:
            self.player.setSource(QUrl.fromLocalFile(file_path))
        else:
            self.player.setMedia(QMediaContent(QUrl.fromLocalFile(file_path)))

        # reset end-guard for a new track
        self._end_guard = False

        self.player.play()

        # Notify MPRIS clients (lock screen / media keys)
        self._mpris_notify(metadata=True, playback=True)

        self.playback_slider.setEnabled(True)
        self.stop_button.setEnabled(True)
        disp = media_info.get("display") or basename(file_path)
        self.status_bar.showMessage(f"Playing: {disp}")

        # Inform lyrics module of current media
        self._lyrics_call("set_media", file_path)
    def stop_song(self):
        self.player.stop()
        self.playback_slider.setValue(0)
        self.playback_slider.setEnabled(False)
        self.stop_button.setEnabled(False)
        self._set_play_button_text("Play")
        self.current_time_label.setText("00:00:00")
        self.status_bar.showMessage("Playback stopped.")
        self._mpris_notify(metadata=True, playback=True)
        self.video_widget.hide()
        self._lyrics_call("clear")
        # prevent watchdog from firing after manual stop
        self._end_guard = True

    def next_song(self):
        if not self.playlist:
            return
        if self.shuffle_mode and len(self.playlist) > 1:
            choices = list(range(len(self.playlist)))
            if 0 <= self.current_song_index < len(self.playlist):
                choices.pop(self.current_song_index)
            self.current_song_index = random.choice(choices)
        else:
            if (self.current_song_index + 1) < len(self.playlist):
                self.current_song_index += 1
            else:
                self.status_bar.showMessage("End of playlist.")
                self.stop_song()
                return
        self.playlist_widget.setCurrentRow(self.current_song_index)
        self.play_song()

    def prev_song(self):
        if not self.playlist:
            return
        if self.shuffle_mode and len(self.playlist) > 1:
            choices = list(range(len(self.playlist)))
            if 0 <= self.current_song_index < len(self.playlist):
                choices.pop(self.current_song_index)
            self.current_song_index = random.choice(choices)
        else:
            if (self.current_song_index - 1) >= 0:
                self.current_song_index -= 1
            else:
                self.status_bar.showMessage("Start of playlist.")
                self.current_song_index = 0
        self.playlist_widget.setCurrentRow(self.current_song_index)
        self.play_song()

    def toggle_shuffle(self):
        self.shuffle_mode = not self.shuffle_mode
        self.shuffle_button.setText("Shuffle ON" if self.shuffle_mode else "Shuffle OFF")
        self.status_bar.showMessage(f"Shuffle Mode: {'ON' if self.shuffle_mode else 'OFF'}")

    def toggle_repeat(self):
        self.repeat_mode = not self.repeat_mode
        self.repeat_button.setText("Repeat ON" if self.repeat_mode else "Repeat OFF")
        self.status_bar.showMessage(f"Repeat Mode: {'ON (Current Track)' if self.repeat_mode else 'OFF'}")

    # ---------------- Volume / Mute ----------------
    def change_volume(self, value: int):
        if USING_QT6:
            self.audio_out.setVolume(max(0.0, min(1.0, value / 100.0)))
        else:
            self.player.setVolume(value)
        self.status_bar.showMessage(f"Volume: {value}%")

    def toggle_mute(self):
        if USING_QT6:
            self.audio_out.setMuted(self.mute_button.isChecked())
        else:
            self.player.setMuted(self.mute_button.isChecked())
        self.status_bar.showMessage("Muted" if self.mute_button.isChecked() else f"Volume: {self.volume_slider.value()}%")

    # ---------------- Slider / time ----------------
    def update_slider(self, position_ms: int):
        # Track real movement (Qt playbackState() can lie on some backends)
        try:
            if position_ms != self._last_pos_ms:
                self._last_pos_ms = int(position_ms)
                self._last_pos_moved_at = time.monotonic()
        except Exception:
            pass        # Throttle slider/UI updates to reduce churn (especially on some backends
        # where positionChanged fires very frequently).
        sec = int(position_ms) // 1000
        should_update_slider = (abs(int(position_ms) - int(getattr(self, "_last_slider_update_ms", -999999))) >= 200) or (sec != int(getattr(self, "_last_ui_second", -1)))

        if should_update_slider:
            self._last_slider_update_ms = int(position_ms)
            self.playback_slider.blockSignals(True)
            self.playback_slider.setValue(position_ms)
            self.playback_slider.blockSignals(False)

        if sec != int(getattr(self, "_last_ui_second", -1)):
            self._last_ui_second = sec
            self.current_time_label.setText(self._millis_to_clock(position_ms))
            # Lyrics updates are typically second-granularity; throttle here too.
            self._lyrics_call("update_position", int(position_ms))

        # Watchdog: if we're within 1s of the end and not advanced yet, advance once.
        if self._duration_ms > 0:
            if position_ms >= max(0, self._duration_ms - 1000) and not self._end_guard:
                self._end_guard = True
                QtCore.QTimer.singleShot(50, self._advance_after_end)


    def set_duration(self, duration_ms: int):
        self._duration_ms = max(0, int(duration_ms))
        self.playback_slider.setRange(0, self._duration_ms)
        self.total_time_label.setText(self._millis_to_clock(self._duration_ms))
        self._end_guard = False

    def seek_position(self, position_ms: int):
        self.player.setPosition(position_ms)
        self.current_time_label.setText(self._millis_to_clock(position_ms))
        self.status_bar.showMessage(f"Seeked to: {self._millis_to_clock(position_ms)}")
        self._lyrics_call("update_position", int(position_ms))

    # ---------------- Media status / errors ----------------
    def handle_media_status(self, status):
        name = getattr(status, 'name', str(status))
        if 'EndOfMedia' in name:
            self._advance_after_end()
        if 'LoadedMedia' in name or 'BufferedMedia' in name:
            self._end_guard = False

    def _advance_after_end(self):
        if self.repeat_mode:
            self.player.setPosition(0)
            self.player.play()
            self._end_guard = False
        else:
            self.next_song()

    def handle_error(self, *args):
        err = ""
        try:
            err = self.player.errorString()
        except Exception:
            err = "Playback error."
        if err:
            QMessageBox.critical(self, "Playback Error", f"An error occurred:\n\n{err}")
            self.stop_song()

    # ---------------- Key handling ----------------
    def keyPressEvent(self, event):
        if event.key() == key('Key_F11'):
            if self.isFullScreen():
                self.showNormal()
                self._show_normal_ui()
            else:
                self.showFullScreen()
                self._hide_ui_for_fullscreen()

        elif event.key() == key('Key_Escape') and self.isFullScreen():
            self.showNormal()
            self._show_normal_ui()

        # NOTE:
        # Do NOT handle Key_P here. Alt+P is already handled by the QAction shortcut.
        # Handling it here causes Alt+P to toggle twice (QAction + keyPressEvent).

        elif event.key() == key('Key_L') and (event.modifiers() & (Qt.KeyboardModifier.ControlModifier if USING_QT6 else Qt.ControlModifier)):
            self.toggle_lyrics()

        else:
            super().keyPressEvent(event)
            
    def _hide_ui_for_fullscreen(self):
        self.playlist_widget.hide()
        self.play_button.hide()
        self.stop_button.hide()
        self.mute_button.hide()
        self.add_button.hide()
        self.prev_button.hide()
        self.next_button.hide()
        self.remove_button.hide()
        self.shuffle_button.hide()
        self.repeat_button.hide()
        self.tab_widget.hide()
        self.lyrics_button.hide()
        self.playback_slider.hide()
        self.theme_combo.hide()
        self.volume_slider.hide()
        self.total_time_label.hide()
        self.current_time_label.hide()
        self.menuBar().hide()

    def _show_normal_ui(self):
        self.playlist_widget.show()
        self.play_button.show()
        self.stop_button.show()
        self.mute_button.show()
        self.add_button.show()
        self.prev_button.show()
        self.next_button.show()
        self.remove_button.show()
        self.shuffle_button.show()
        self.repeat_button.show()
        self.tab_widget.show()
        self.lyrics_button.show()
        self.playback_slider.show()
        self.theme_combo.show()
        self.volume_slider.show()
        self.total_time_label.show()
        self.current_time_label.show()
        self.menuBar().show()
        
    # ---------------- Helpers ----------------
    def _update_controls_enabled(self):
        enabled = bool(self.playlist)
        for w in (self.play_button, self.remove_button, self.prev_button,
                  self.next_button, self.shuffle_button, self.repeat_button):
            w.setEnabled(enabled)

    def player_is_playing(self) -> bool:
        """Helper for MPRIS: return True if Qt player is currently playing."""
        try:
            if USING_QT6:
                from PyQt6.QtMultimedia import QMediaPlayer as QMP
                return self.player.playbackState() == QMP.PlaybackState.PlayingState
            else:
                st = self.player.state()
                try:
                    return st == QMediaPlayer.State.PlayingState
                except Exception:
                    return st == getattr(QMediaPlayer, 'PlayingState', st)
        except Exception:
            return False
    def _is_effectively_playing(self) -> bool:
        """
        More reliable than playbackState() on some systems.
        If the position has moved very recently, we treat it as playing.

        Important: if the backend explicitly reports Paused, we treat it as paused
        (even if the position moved a moment ago).
        """
        try:
            # Trust explicit "Playing"
            if self.player_is_playing():
                return True

            # Trust explicit "Paused" if available
            try:
                if USING_QT6:
                    from PyQt6.QtMultimedia import QMediaPlayer as QMP
                    if self.player.playbackState() == QMP.PlaybackState.PausedState:
                        return False
                else:
                    st = self.player.state()
                    try:
                        if st == QMediaPlayer.State.PausedState:
                            return False
                    except Exception:
                        if st == getattr(QMediaPlayer, "PausedState", st):
                            return False
            except Exception:
                pass

            # If we saw position move in the last ~0.7s, it is effectively playing.
            moved_recently = (time.monotonic() - float(getattr(self, "_last_pos_moved_at", 0.0))) < 0.7
            return bool(moved_recently and self.player.position() > 0)
        except Exception:
            return False


    def _build_media_meta(self, file_path: str, mtype: str = 'audio') -> dict:
        """Return parsed media metadata for display/MPRIS.

        Keeps dependencies optional: if mutagen is available, use tags;
        otherwise fall back to filename heuristics.
        """
        artist = ""
        title = ""
        display = ""

        # Basic from filename
        try:
            base = basename(file_path or "")
            stem = splitext(base)[0]
            title = stem
            for sep in (" — ", " - ", " – ", "_-"):
                if sep in stem:
                    a, t = stem.split(sep, 1)
                    artist = a.strip()
                    title = t.strip()
                    break
        except Exception:
            title = basename(file_path or "")

        # Optional tag read (audio)
        if mtype == 'audio':
            try:
                from mutagen import File as MutagenFile  # type: ignore
                mf = MutagenFile(file_path, easy=True)
                if mf:
                    t = (mf.get('title') or [None])[0]
                    a = (mf.get('artist') or [None])[0]
                    if t:
                        title = str(t)
                    if a:
                        artist = str(a)
            except Exception:
                pass

        title = title or "Unknown"
        display = f"{artist} — {title}" if artist else title
        return {"artist": artist, "title": title, "display": display}

    def eventFilter(self, obj, event):
        """Global key handler (when app focused) for Space / media keys.

        Alt+P is handled by QAction shortcut; this covers Space/media keys and
        avoids toggling while typing in text fields.
        """
        try:
            et = event.type()
            if USING_QT6:
                is_keypress = (et == QtCore.QEvent.Type.KeyPress)
            else:
                is_keypress = (et == QtCore.QEvent.KeyPress)

            if is_keypress:
                fw = QtWidgets.QApplication.focusWidget()
                if isinstance(fw, QLineEdit):
                    return False

                k = event.key()
                keys = []
                for nm in ('Key_Space', 'Key_MediaTogglePlayPause', 'Key_MediaPlay', 'Key_MediaPause'):
                    try:
                        keys.append(key(nm))
                    except Exception:
                        pass

                if k in keys:
                    self._toggle_play_pause_shortcut()
                    return True
        except Exception:
            pass

        return super().eventFilter(obj, event)

    def _mpris_notify(self, metadata: bool = False, playback: bool = False):
        """Notify MPRIS clients about playback and/or metadata changes."""
        m = getattr(self, 'mpris', None)
        if not m:
            return
        try:
            if playback:
                fn = getattr(m, 'notify_playback', None)
                if callable(fn):
                    fn()
            if metadata:
                fn = getattr(m, 'notify_metadata', None)
                if callable(fn):
                    fn()
            # Always pump GLib/DBus so changes flush quickly
            fn = getattr(m, 'poll', None)
            if callable(fn):
                fn()
        except Exception:
            pass

    def _set_play_button_text(self, txt: str):
        self.play_button.setText(txt)

    def _millis_to_clock(self, millis: int) -> str:
        """Format milliseconds as HH:MM:SS for the UI."""
        s = max(0, int(millis) // 1000)
        h = s // 3600
        m = (s % 3600) // 60
        s = s % 60
        return f"{h:02}:{m:02}:{s:02}"

    # Backward-compatible helper (older code paths)
    def _millis_to_time(self, millis: int) -> str:
        return self._millis_to_clock(millis)


    # ---------------- Lyrics control (toggle via button) ----------------
    def toggle_lyrics(self):
        if not getattr(self, "lyrics", None):
            return
        try:
            visible_now = bool(self.lyrics.isVisible())
        except Exception:
            visible_now = bool(getattr(self, "_lyrics_visible", False))
        if visible_now:
            self.hide_lyrics()
        else:
            self.show_lyrics()

    def show_lyrics(self):
        if not getattr(self, "lyrics", None):
            return
        try:
            self.lyrics.show_panel()
            self._lyrics_visible = True
            if hasattr(self, "lyrics_button"):
                self.lyrics_button.setChecked(True)
                self.lyrics_button.setText("Hide Lyrics")
            # ensure enough width for the dock
            if self.width() < 1200:
                self.resize(1200, self.height())
            _safe_bring_to_front(self)
        except Exception:
            pass

    def hide_lyrics(self):
        if not getattr(self, "lyrics", None):
            return
        try:
            self.lyrics.hide_panel()
            self._lyrics_visible = False
            if hasattr(self, "lyrics_button"):
                self.lyrics_button.setChecked(False)
                self.lyrics_button.setText("Lyrics")
        except Exception:
            pass

    def _lyrics_call(self, name: str, *args):
        """Call an optional lyrics API; ignore errors."""
        if not getattr(self, "lyrics", None):
            return
        fn = getattr(self.lyrics, name, None)
        if callable(fn):
            try:
                fn(*args)
            except Exception as e:
                print(f"lyrics.{name} error:", e)
    # ---------------- Custom stream URL ----------------
    def add_custom_stream(self):
        """Add a custom stream URL as a playlist item."""
        name = self.custom_station_name.text().strip()
        url = self.custom_station_url.text().strip()
        if not name or not url:
            QMessageBox.warning(self, "Invalid Input", "Stream name and URL cannot be empty.")
            return
        if any(it.get("type") == "stream" and it.get("path") == url for it in self.playlist):
            QMessageBox.information(self, "Already Added", "This stream URL is already in the playlist.")
            return

        item = {"path": url, "type": "stream", "artist": "", "title": name, "display": name}
        self.playlist.append(item)
        self.playlist_widget.addItem(name)
        self.custom_station_name.clear()
        self.custom_station_url.clear()
        self._update_controls_enabled()



    # ---------------- State change adapters ----------------
    def _on_playback_state_changed(self, state):
        """Qt6: playbackStateChanged adapter (and safe no-op on Qt5)."""
        playing = False
        try:
            if USING_QT6:
                from PyQt6.QtMultimedia import QMediaPlayer as _QMP  # type: ignore
                playing = (state == _QMP.PlaybackState.PlayingState)
            else:
                playing = bool(self.player_is_playing())
        except Exception:
            playing = bool(getattr(self, "_last_playing", False))

        self._last_playing = playing
        self._mpris_last_playing = playing

        self._set_play_button_text("Pause" if playing else "Play")
        self._lyrics_call("set_playing", playing)
        self._update_controls_enabled()

        # Clear any temporary forced OSD state
        try:
            m = getattr(self, "mpris", None)
            fn = getattr(m, "clear_forced_playback_status", None) if m else None
            if callable(fn):
                fn()
        except Exception:
            pass

        self._mpris_notify(playback=True)

    def _on_state_changed(self, state):
        """Qt5: stateChanged adapter (and safe fallback on Qt6)."""
        playing = False
        try:
            if USING_QT6:
                playing = bool(self.player_is_playing())
            else:
                try:
                    playing = (state == QMediaPlayer.State.PlayingState)
                except Exception:
                    playing = (state == getattr(QMediaPlayer, "PlayingState", state))
        except Exception:
            playing = bool(getattr(self, "_last_playing", False))

        self._last_playing = playing
        self._mpris_last_playing = playing

        self._set_play_button_text("Pause" if playing else "Play")
        self._lyrics_call("set_playing", playing)
        self._update_controls_enabled()

        # Clear any temporary forced OSD state
        try:
            m = getattr(self, "mpris", None)
            fn = getattr(m, "clear_forced_playback_status", None) if m else None
            if callable(fn):
                fn()
        except Exception:
            pass

        self._mpris_notify(playback=True)

    def closeEvent(self, event):
        """Stop timers, playback, and helper modules cleanly."""
        # Stop MPRIS polling
        try:
            t = getattr(self, "_mpris_timer", None)
            if t:
                t.stop()
        except Exception:
            pass

        # Stop playback
        try:
            self.player.stop()
        except Exception:
            pass

        # Stop lyrics worker thread cleanly
        try:
            lyr = getattr(self, "lyrics", None)
            fn = getattr(lyr, "shutdown", None) if lyr else None
            if callable(fn):
                fn()
        except Exception:
            pass

        # Let MPRIS quit hook be a no-op
        try:
            m = getattr(self, "mpris", None)
            fn = getattr(m, "stop", None) if m else None
            if callable(fn):
                fn()
        except Exception:
            pass

        super().closeEvent(event)

# ---------------- Main ----------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec() if USING_QT6 else app.exec_())
