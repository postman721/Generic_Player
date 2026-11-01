#!/usr/bin/env python3
"""Application entry point and playback controller for Generic Player.

MainWindow owns the queue and Qt media player; PlayerExperienceMixin supplies
the presentation and session hooks. Optional lyrics and MPRIS bridges observe
that state rather than maintaining a second playback controller.

Command-line help/version are parsed before importing Qt, so installation
diagnostics work without a graphical or audio runtime.

License: GPL v2. Author: JJ Posti <techtimejourney.net>.
"""

import argparse
import sys
sys.dont_write_bytecode = True
import os
import json
import random
import time
from os.path import basename, splitext
from generic_player_version import __version__
from generic_player_library import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS, decode_local_file_url, validate_stream_url


def _parse_command_line(argv):
    """Normalize desktop file URLs before they enter the ordinary import path."""
    parser = argparse.ArgumentParser(
        prog="generic-player", description="Play local music, video and HTTP(S) media streams.")
    parser.add_argument("--version", action="version", version=f"Generic Player {__version__}")
    parser.add_argument("sources", nargs="*", metavar="SOURCE",
                        help="media file, folder, playlist, local file URL or HTTP(S) stream")
    arguments = parser.parse_args(argv)
    normalized = []
    for source in arguments.sources:
        try:
            if source.lower().startswith("file:"):
                source = decode_local_file_url(source)
            elif source.lower().startswith(("http:", "https:")):
                source = validate_stream_url(source)
            elif "://" in source:
                raise ValueError("only local files and HTTP(S) media URLs are supported")
        except (ValueError, UnicodeError) as error:
            parser.error(f"{source!r}: {error}")
        normalized.append(source)
    arguments.sources = normalized
    return arguments


# Informational commands work before importing the graphical/audio runtime.
_script_arguments = _parse_command_line(sys.argv[1:]) if __name__ == "__main__" else None

from theme import ThemeManager
from generic_player_qt import QtCore, QtGui, QtWidgets, QtMultimedia, QtMultimediaWidgets, Qt, QAction, USING_QT6, stop_media_player
from generic_player_ui import PlayerExperienceMixin
import generic_player_mpris
import generic_player_lyrics
from generic_player_metadata import build_media_metadata, read_player_metadata, safe_metadata_text
from generic_player_osd import DesktopOSD

QUrl, QSize = QtCore.QUrl, QtCore.QSize
QIcon, QKeySequence = QtGui.QIcon, QtGui.QKeySequence
QApplication, QMainWindow, QWidget = QtWidgets.QApplication, QtWidgets.QMainWindow, QtWidgets.QWidget
QVBoxLayout, QHBoxLayout, QPushButton = QtWidgets.QVBoxLayout, QtWidgets.QHBoxLayout, QtWidgets.QPushButton
QListWidget, QFileDialog, QSlider = QtWidgets.QListWidget, QtWidgets.QFileDialog, QtWidgets.QSlider
QAbstractItemView, QMessageBox, QLabel = QtWidgets.QAbstractItemView, QtWidgets.QMessageBox, QtWidgets.QLabel
QTabWidget, QLineEdit, QStatusBar = QtWidgets.QTabWidget, QtWidgets.QLineEdit, QtWidgets.QStatusBar
QMenuBar, QComboBox = QtWidgets.QMenuBar, QtWidgets.QComboBox
QMediaPlayer, QVideoWidget = QtMultimedia.QMediaPlayer, QtMultimediaWidgets.QVideoWidget
if USING_QT6:
    QAudioOutput = QtMultimedia.QAudioOutput
else:
    QMediaContent = QtMultimedia.QMediaContent

# ---------------- Cross-version helpers ----------------
def align_center():
    return Qt.AlignmentFlag.AlignCenter if USING_QT6 else Qt.AlignCenter

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
class MainWindow(PlayerExperienceMixin, QMainWindow):
    """Coordinate GUI-thread playback and invalidate obsolete callbacks.

    ``_source_generation`` invalidates deferred end detection after loads,
    seeks, and Stop. ``_play_request`` identifies one load attempt separately:
    applying its saved seek may change the generation without replacing it.
    ``_end_guard`` coalesces completion signals, while ``_manual_stop`` keeps
    late backend signals from restarting explicitly stopped playback.
    """

    SUPPORTED_VIDEO_EXTENSIONS = VIDEO_EXTENSIONS
    SUPPORTED_AUDIO_EXTENSIONS = AUDIO_EXTENSIONS

    def __init__(self):
        super().__init__()

        # Window
        self.setWindowTitle("Generic Player")
        self.setGeometry(100, 100, 1180, 780)
        self.setMinimumSize(920, 660)
        self._session_ready = False
        self._closing = False
        self.setAcceptDrops(True)

        # Last played state 
        self._last_playing = False


        # UI throttles (reduce label/slider churn)
        self._last_ui_second = -1
        self._last_slider_update_ms = -999999
        # Playlist & state
        self.playlist = []
        self.current_song_index = -1
        self.shuffle_mode = False
        self.repeat_mode = "off"
        self.current_media_type = 'audio'
        self._lyrics_visible = False

        # Track-end watchdog
        self._duration_ms = 0
        self._end_guard = False

        # Some physical media keys deliver duplicate toggle events. Ordinary
        # key presses, mouse clicks and remote commands remain independent.
        self._toggle_guard_until = 0.0

        self.mpris = None
        self.osd = None
        self._osd_mode = "auto"

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
        # Coalesce tag updates from a loading decoder. A newer source replaces
        # the pending request, so late metadata cannot rename another track.
        self._metadata_timer = QtCore.QTimer(self)
        self._metadata_timer.setSingleShot(True)
        self._metadata_timer.setInterval(80)
        self._metadata_timer.timeout.connect(self._update_media_metadata)
        self.player.metaDataChanged.connect(self._schedule_media_metadata)

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

        self.player.seekableChanged.connect(self._sync_seek_enabled)
        self.player.seekableChanged.connect(self._resume_when_ready)
        self.player.durationChanged.connect(self._resume_when_ready)
        self._restore_session()

        # ---- MPRIS / playerctl support ----
        self.mpris = None
        try:
            self.mpris = generic_player_mpris.GenericPlayerMPRIS(self)
        except Exception as e:
            self.mpris = None
            print("MPRIS disabled:", e)
        # Notifications are a separate, optional consumer of player state.
        # Restoring a session never announces a track that has not been played.
        self.osd = DesktopOSD(self, mode=self._osd_mode)
        self.osd.unavailable.connect(self._on_osd_unavailable)
        self.osd.selected.connect(self._on_osd_selected)

    # ---------------- Drag & Drop ----------------
    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        if event.mimeData().hasUrls():
            sources = [url.toLocalFile() if url.isLocalFile() else url.toString()
                       for url in event.mimeData().urls()]
            self._process_dropped_files(sources)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def _process_dropped_files(self, files):
        """Add files/folders and dropped playlists without losing the active queue."""
        from generic_player_library import (
            collect_media_paths, load_playlist_file, media_type, normalize_entry,
            MAX_PLAYLIST_ENTRIES, PLAYLIST_EXTENSIONS, validate_stream_url,
        )
        local_sources, entries, skipped = [], [], 0
        known = {entry["path"] for entry in self.playlist}
        available = max(0, MAX_PLAYLIST_ENTRIES - len(self.playlist))
        limit_reached = False

        def stage_entry(entry):
            nonlocal limit_reached
            if entry["path"] not in known:
                # Imported and manually added queues must obey the same bound
                # as session persistence, or an apparently successful addition
                # leaves a queue that cannot be saved.
                if len(entries) >= available:
                    limit_reached = True
                    return
                known.add(entry["path"])
                entries.append(entry)

        for source in files:
            if not isinstance(source, str):
                skipped += 1
                continue
            if source.lower().startswith(("http://", "https://")):
                try:
                    stage_entry(normalize_entry({"path": validate_stream_url(source), "type": "stream"}))
                except ValueError:
                    skipped += 1
            elif splitext(source)[1].lower() in PLAYLIST_EXTENSIONS and os.path.isfile(source):
                try:
                    for entry in load_playlist_file(source):
                        stage_entry(entry)
                except (ValueError, OSError) as exc:
                    self.status_bar.showMessage(f"Could not import {basename(source)}: {exc}", 7000)
                    skipped += 1
            else:
                local_sources.append(source)
        paths, ignored = collect_media_paths(local_sources)
        skipped += ignored
        for source in paths:
            # Imports can overlap the existing queue or a playlist in this
            # same drop. Avoid opening media tags for entries we will discard.
            if source in known:
                continue
            if len(entries) >= available:
                limit_reached = True
                break
            kind = media_type(source)
            stage_entry(normalize_entry({"path": source, "type": kind,
                                         **self._build_media_meta(source, kind)}))
        self.playlist.extend(entries)
        added = len(entries)
        if added:
            self._queue_changed()
        else:
            self._update_controls_enabled()
        message = f"Added {added} {'track' if added == 1 else 'tracks'}."
        if limit_reached:
            message += f" Queue limit of {MAX_PLAYLIST_ENTRIES:,} tracks reached; additional tracks were not added."
        if skipped:
            message += f" Skipped {skipped} unsupported or unavailable {'item' if skipped == 1 else 'items'}."
        elif not added and not limit_reached:
            message = "No new media found. These tracks may already be in your queue."
        self.status_bar.showMessage(message, 6000)
        return added

    def _toggle_guard_active(self, window_ms: int = 220) -> bool:
        """Suppress closely duplicated physical media-toggle events only."""
        now = time.monotonic()
        try:
            until = float(getattr(self, "_toggle_guard_until", 0.0))
        except Exception:
            until = 0.0

        if now < until:
            return True

        self._toggle_guard_until = now + (max(0, int(window_ms)) / 1000.0)
        return False
        
    def _toggle_play_pause_shortcut(self):
        """Handle one accepted keyboard gesture without discarding a later one."""
        self.play_pause_song()

    def _toggle_media_play_pause_shortcut(self):
        """Keep hardware duplicate filtering separate from ordinary controls."""
        if self.playlist and not self._toggle_guard_active():
            self.play_pause_song()

    def apply_theme(self, theme_name: str):
        """Apply a named theme (and persist it)."""
        theme_name = (theme_name or ThemeManager.DEFAULT_THEME).strip()
        if theme_name not in ThemeManager.themes():
            theme_name = ThemeManager.DEFAULT_THEME

        self._current_theme = theme_name
        self.setStyleSheet(ThemeManager.qss(theme_name))
        palette = ThemeManager.palette(theme_name)
        self.visualizer.set_colors(palette["accent"], palette["muted"], palette["border"])
        self.album_artwork.set_colors(palette["accent"], palette["raised"])
        self._apply_ui_palette(palette)
        self.playlist_widget.viewport().update()

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
        from generic_player_library import save_playlist_file
        if not self.playlist:
            self.status_bar.showMessage("Add some music before saving a playlist.", 4000)
            return
        filename, selected_filter = QFileDialog.getSaveFileName(
            self, "Save playlist", "playlist.m3u8",
            "M3U8 playlist (*.m3u8);;M3U playlist (*.m3u);;JSON playlist (*.json)",
            options=file_dialog_options(False))
        if not filename:
            return
        if not splitext(filename)[1]:
            filename += ".json" if "JSON" in selected_filter else ".m3u" if "(*.m3u)" in selected_filter else ".m3u8"
        try:
            save_playlist_file(filename, self.playlist)
            self.status_bar.showMessage(f"Playlist saved: {basename(filename)}", 5000)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not save playlist", str(exc))

    def load_playlist(self):
        from generic_player_library import load_playlist_file
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open playlist", "", "Playlists (*.m3u8 *.m3u *.json);;All files (*)",
            options=file_dialog_options(False))
        if not filename:
            return
        try:
            entries = load_playlist_file(filename)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not open playlist", str(exc))
            return
        # Validate the complete document before changing or stopping the current queue.
        self.stop_song()
        self._loaded_media_path = None
        self._loaded_media_id = None
        self.current_song_index = -1
        self.playlist = entries
        self._queue_changed()
        self.status_bar.showMessage(f"Loaded {len(entries)} tracks from {basename(filename)}", 5000)

    def add_songs(self):
        from generic_player_library import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
        patterns = " ".join("*" + extension for extension in sorted(AUDIO_EXTENSIONS | VIDEO_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add media files", "", f"Media files ({patterns});;All files (*)",
            options=file_dialog_options(False))
        if files:
            self._process_dropped_files(files)

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Add music folder", "",
                                                  options=file_dialog_options(False))
        if folder:
            self._process_dropped_files([folder])

    def _playlist_role(self):
        return Qt.ItemDataRole.UserRole if USING_QT6 else Qt.UserRole

    def _refresh_playlist(self):
        """Rebuild rows with stable identities, preserving selection and filtering."""
        import uuid
        role = self._playlist_role()
        selected = {item.data(role) for item in self.playlist_widget.selectedItems()}
        self.playlist_widget.blockSignals(True)
        try:
            self.playlist_widget.clear()
            identities = set()
            for entry in self.playlist:
                identity = entry.get("id")
                if not isinstance(identity, str) or not identity or identity in identities:
                    identity = uuid.uuid4().hex
                    entry["id"] = identity
                identities.add(identity)
                title = entry.get("display") or entry.get("title") or basename(entry.get("path", ""))
                row = QtWidgets.QListWidgetItem(title)
                row.setData(role, identity)
                row.setToolTip(entry.get("path", ""))
                row.setSizeHint(QSize(0, 62))
                self.playlist_widget.addItem(row)
                if identity in selected:
                    row.setSelected(True)
            if 0 <= self.current_song_index < self.playlist_widget.count():
                flag = QtCore.QItemSelectionModel.SelectionFlag.NoUpdate if USING_QT6 else QtCore.QItemSelectionModel.NoUpdate
                self.playlist_widget.setCurrentRow(self.current_song_index, flag)
        finally:
            self.playlist_widget.blockSignals(False)
        search = self.search_input.text() if hasattr(self, "search_input") else ""
        self.filter_playlist(search)

    def _queue_changed(self):
        self._refresh_playlist()
        reset = getattr(self, "_reset_queue_navigation", None)
        if callable(reset):
            reset()
        self._update_controls_enabled()
        self._mpris_notify(metadata=True, playback=True)
        save = getattr(self, "_save_session", None)
        if callable(save):
            save()

    def filter_playlist(self, text):
        terms = str(text).casefold().split()
        visible = 0
        for index, entry in enumerate(self.playlist):
            row = self.playlist_widget.item(index)
            if row is None:
                continue
            haystack = " ".join(str(entry.get(field, "")) for field in ("title", "artist", "album", "display", "path")).casefold()
            matches = all(term in haystack for term in terms)
            row.setHidden(not matches)
            if not matches:
                row.setSelected(False)
            visible += int(matches)
        if hasattr(self, "queue_count_label"):
            count = len(self.playlist)
            self.queue_count_label.setText(f"{visible} of {count} tracks" if terms else f"{count} {'track' if count == 1 else 'tracks'}")
        if hasattr(self, "empty_queue_label"):
            self.empty_queue_label.setText("No matches. Try another search." if self.playlist else "Your queue is ready for music.\nAdd files, a folder, or drop them here.")
            self.empty_queue_label.setVisible(visible == 0)
        # Dragging filtered rows has ambiguous placement; enable it for the complete queue.
        self.playlist_widget.setDragEnabled(not bool(terms))

    def on_playlist_reordered(self, *args):
        """Keep list order and playing identity in sync after Qt moves its rows."""
        role = self._playlist_role()
        lookup = {entry["id"]: entry for entry in self.playlist}
        ordered_ids = [self.playlist_widget.item(i).data(role) for i in range(self.playlist_widget.count())]
        if len(ordered_ids) != len(self.playlist) or set(ordered_ids) != set(lookup):
            QtCore.QTimer.singleShot(0, self._refresh_playlist)
            return
        current_id = self.playlist[self.current_song_index]["id"] if 0 <= self.current_song_index < len(self.playlist) else None
        self.playlist = [lookup[identity] for identity in ordered_ids]
        self.current_song_index = ordered_ids.index(current_id) if current_id in ordered_ids else -1
        reset = getattr(self, "_reset_queue_navigation", None)
        if callable(reset):
            reset()
        self._update_controls_enabled()
        self._mpris_notify(metadata=True)
        save = getattr(self, "_save_session", None)
        if callable(save):
            save()

    def remove_songs(self):
        rows = sorted({self.playlist_widget.row(item) for item in self.playlist_widget.selectedItems()
                       if not item.isHidden()}, reverse=True)
        if not rows:
            return
        current = self.current_song_index
        if current in rows:
            self.stop_song()
            self._loaded_media_path = None
            self._loaded_media_id = None
            self.current_song_index = -1
        elif current >= 0:
            self.current_song_index = current - sum(index < current for index in rows)
        for index in rows:
            if 0 <= index < len(self.playlist):
                self.playlist.pop(index)
        self._queue_changed()
        self.status_bar.showMessage(f"Removed {len(rows)} {'track' if len(rows) == 1 else 'tracks'} from the queue.", 4000)

    def clear_playlist(self):
        if not self.playlist:
            return
        self.stop_song()
        self._loaded_media_path = None
        self._loaded_media_id = None
        self.current_song_index = -1
        self.playlist.clear()
        self._queue_changed()
        self.status_bar.showMessage("Queue cleared.", 4000)

    def play_selected_song(self, item=None):
        row = self.playlist_widget.row(item) if item is not None else self.playlist_widget.currentRow()
        if not 0 <= row < len(self.playlist):
            return
        if self.playlist_widget.item(row).isHidden():
            return
        self.current_song_index = row
        self.play_song()

    # ---------------- Playback control ----------------


    def play_pause_song(self, prefer_last_state: bool = False):
        """Toggle once, trusting an explicit backend state over stale UI flags."""
        if not self.playlist:
            return
        state = self._desktop_playback_status()
        if state == "Playing":
            self._pause_playback()
        elif state == "Paused":
            self._resume_playback()
        else:
            if not 0 <= self.current_song_index < len(self.playlist):
                row = self.playlist_widget.currentRow()
                self.current_song_index = row if 0 <= row < len(self.playlist) else 0
            self.play_song()
        self._mpris_notify(playback=True)
        self._refresh_playback_ui()

    def _resume_playback(self):
        self._manual_stop = False
        self._pending_playback_state = "Playing"
        self._pending_playback_request = getattr(self, "_play_request", None)
        self.player.play()

    def _pause_playback(self):
        self._pending_playback_state = "Paused"
        self._pending_playback_request = getattr(self, "_play_request", None)
        self.player.pause()

    def _desktop_playback_status(self):
        """Report the source's state, including an accepted load-time command.

        Qt can stay Stopped until decoding has opened a newly selected source.
        Keep its requested state tied to that exact load so rapid Play/Pause or
        Next commands remain meaningful. Stop, errors and new loads invalidate
        it; backend state signals take over as soon as decoding is ready.
        """
        states = QMediaPlayer.PlaybackState if USING_QT6 else QMediaPlayer
        state = self.player.playbackState() if USING_QT6 else self.player.state()
        status = {states.PlayingState: "Playing", states.PausedState: "Paused"}.get(state, "Stopped")
        if status != "Stopped" or getattr(self, "_manual_stop", False):
            return status
        pending = getattr(self, "_pending_playback_state", None)
        request = getattr(self, "_pending_playback_request", None)
        statuses = QMediaPlayer.MediaStatus if USING_QT6 else QMediaPlayer
        if (pending in {"Playing", "Paused"} and request is not None
                and request is getattr(self, "_play_request", None)
                and self.player.mediaStatus() in (statuses.LoadingMedia, statuses.LoadedMedia,
                                                  statuses.BufferingMedia, statuses.StalledMedia)):
            return pending
        return status

    def _refresh_playback_ui(self):
        callback = getattr(self, "_on_playback_ui_changed", None)
        if callable(callback):
            callback()

    def _repeat_mode(self):
        """Accept the old boolean setting when upgrading a saved session."""
        mode = self.repeat_mode
        if mode is True:
            return "one"
        return mode if mode in ("off", "all", "one") else "off"

    def _queue_signature(self):
        return tuple((entry.get("path", ""), entry.get("type", "audio"))
                     for entry in self.playlist)

    def _reset_queue_navigation(self):
        # Queue mutations invalidate the bag. Build it only when shuffle is
        # actually used, and release its signature/history when shuffle is off.
        self._shuffle_queue = None

    def _navigation(self):
        if getattr(self, "_shuffle_queue", None) is None:
            from generic_player_queue import ShuffleHistory
            self._shuffle_queue = ShuffleHistory()
            self._shuffle_queue.reset(self._queue_signature(), self.current_song_index)
        else:
            # Add/remove/load/reorder all call _reset_queue_navigation. Between
            # edits, only selection changes; rescanning the queue on every Next
            # would turn one shuffled pass through a large queue quadratic.
            self._shuffle_queue.select(self.current_song_index)
        return self._shuffle_queue

    def _restore_loaded_selection(self):
        loaded = getattr(self, "_loaded_media_path", None)
        loaded_id = getattr(self, "_loaded_media_id", None)
        self.current_song_index = next(
            (i for i, item in enumerate(self.playlist)
             if (item.get("id") == loaded_id if loaded_id else item.get("path") == loaded)), -1)
        self.playlist_widget.setCurrentRow(self.current_song_index)

    def play_song(self, playback_state="Playing"):
        """Load the selected entry; return whether the source was accepted."""
        if not 0 <= self.current_song_index < len(self.playlist):
            return False
        media_info = self.playlist[self.current_song_index]
        file_path = media_info.get("path", "")
        mtype = media_info.get("type", "audio")
        if mtype == "stream":
            url = QUrl(file_path)
            if not url.isValid() or url.scheme().lower() not in ("http", "https") or not url.host():
                self._restore_loaded_selection()
                self.status_bar.showMessage("Cannot play this stream: enter a valid HTTP or HTTPS URL.")
                return False
        else:
            if not file_path or not os.path.isfile(file_path):
                self._restore_loaded_selection()
                self.status_bar.showMessage(f"File not found: {file_path or 'empty path'}. Add the file again or remove it from the queue.")
                return False
            url = QUrl.fromLocalFile(os.path.abspath(file_path))

        if any(field not in media_info for field in ("display", "title", "artist")):
            media_info.update(self._build_media_meta(file_path, mtype))
        elif mtype == "audio" and not (media_info.get("year") or media_info.get("date")):
            # Older saved queues did not retain dates. Fill the current entry
            # lazily instead of rereading every file during session startup.
            tags = self._build_media_meta(file_path, mtype)
            for field in ("year", "date"):
                if tags.get(field):
                    media_info[field] = tags[field]

        # Invalidate deferred work for the old source before asking Qt to load.
        self._source_generation = getattr(self, "_source_generation", 0) + 1
        # Seek operations also change the end-callback generation. Track this
        # load separately so a synchronous bookmark seek remains valid while a
        # reentrant source replacement cancels the obsolete outer request.
        play_request = self._play_request = object()
        self._pending_playback_state = playback_state if playback_state in {"Playing", "Paused"} else None
        self._pending_playback_request = play_request
        self._manual_stop = False
        self._end_guard = True
        self._awaiting_repeat_restart = False
        # A seek waiting for the previous source's readiness must not run on
        # synchronous duration/seekable signals from setSource(). The selected
        # source's saved bookmark is armed later by _on_track_loaded.
        self._resume_pending = 0
        self._duration_ms = 0
        self._last_ui_second = -1
        self._last_slider_update_ms = -999999
        self._last_pos_ms = 0
        self._last_pos_moved_at = 0.0
        self.current_media_type = mtype
        self._loaded_media_path = file_path
        self._loaded_media_id = media_info.get("id")
        self.playlist_widget.setCurrentRow(self.current_song_index)
        # Reset presentation without calling the seek slot on the old source.
        was_blocked = self.playback_slider.blockSignals(True)
        try:
            self.playback_slider.setRange(0, 0)
        finally:
            self.playback_slider.blockSignals(was_blocked)
        self.playback_slider.setEnabled(False)
        self.current_time_label.setText(self._millis_to_clock(0))
        self.total_time_label.setText("LIVE" if mtype == "stream" else self._millis_to_clock(0))
        self.video_widget.setVisible(mtype == "video")
        generation = self._source_generation
        if USING_QT6:
            self.player.setSource(url)
        else:
            self.player.setMedia(QMediaContent(url))
        # Qt may report a decoder error synchronously. handle_error()/Stop
        # already performed cleanup; do not overwrite it or restart playback.
        if generation != self._source_generation or getattr(self, "_manual_stop", False):
            return False
        self._end_guard = False
        if self.shuffle_mode:
            self._navigation()
        callback = getattr(self, "_on_track_loaded", None)
        if callable(callback):
            callback(media_info)
        if (self._play_request is not play_request or getattr(self, "_manual_stop", False)
                or getattr(self, "_closing", False)):
            return False
        if playback_state == "Paused":
            self._pause_playback()
        elif playback_state == "Stopped":
            self._manual_stop = True
            self._end_guard = True
            self._resume_pending = 0
        else:
            self._resume_playback()
        if (self._play_request is not play_request
                or (getattr(self, "_manual_stop", False) and playback_state != "Stopped")):
            return False
        self.stop_button.setEnabled(playback_state != "Stopped")
        self._sync_seek_enabled()
        self._mpris_notify(metadata=True, playback=True)
        display = media_info.get("display") or basename(file_path)
        status = ("Streaming" if mtype == "stream" else "Playing") if playback_state == "Playing" else playback_state
        self.status_bar.showMessage(f"{status}: {display}")
        if mtype == "stream":
            self._lyrics_call("clear")
        else:
            self._lyrics_call("set_media", file_path)
        self._refresh_playback_ui()
        return True

    def stop_song(self):
        # Guard first: stop() may emit position/status changes synchronously.
        self._manual_stop = True
        self._pending_playback_state = None
        self._pending_playback_request = None
        self._end_guard = True
        self._awaiting_repeat_restart = False
        # Readiness signals can arrive after stop(), including synchronously.
        # An explicit Stop cancels both saved and still-loading resume points.
        self._resume_position = None
        self._resume_pending = 0
        self._source_generation = getattr(self, "_source_generation", 0) + 1
        stop_media_player(self.player)
        self._last_playing = False
        self._last_pos_moved_at = 0.0
        self._last_ui_second = -1
        # valueChanged is the user's seek path. Resetting this display must
        # never start a new native seek after the decoder has just stopped.
        was_blocked = self.playback_slider.blockSignals(True)
        try:
            self.playback_slider.setValue(0)
        finally:
            self.playback_slider.blockSignals(was_blocked)
        self.playback_slider.setEnabled(False)
        self.stop_button.setEnabled(False)
        self._set_play_button_text("Play")
        self.current_time_label.setText(self._millis_to_clock(0))
        self.status_bar.showMessage("Playback stopped.")
        self._mpris_notify(metadata=True, playback=True)
        self.video_widget.hide()
        self._lyrics_call("clear")
        self._refresh_playback_ui()

    def next_song(self):
        self._next_song("Playing")

    def _next_song(self, playback_state):
        if not self.playlist:
            return
        repeat_all = self._repeat_mode() == "all"
        if self.shuffle_mode:
            index = self._navigation().next(repeat_all=repeat_all)
        elif self.current_song_index + 1 < len(self.playlist):
            index = max(0, self.current_song_index + 1)
        else:
            index = 0 if repeat_all else None
        if index is None:
            self.stop_song()
            self.status_bar.showMessage("End of queue. Enable Repeat all to keep listening.")
            return
        self.current_song_index = index
        self.play_song(playback_state)

    def prev_song(self):
        self._prev_song("Playing")

    def _prev_song(self, playback_state):
        if not self.playlist:
            return
        # A second press goes to the previous track; the first restarts a track
        # that has already played for more than three seconds.
        if self.player.position() > 3000 and self.player.isSeekable():
            self.seek_position(0)
            return
        if self.shuffle_mode:
            index = self._navigation().previous()
            if index is None:
                index = 0
        elif self.current_song_index > 0:
            index = self.current_song_index - 1
        else:
            index = len(self.playlist) - 1 if self._repeat_mode() == "all" else 0
        self.current_song_index = index
        self.play_song(playback_state)

    def _skip_from_remote(self, method_name, playback_state):
        """Skip through the normal queue while preserving the remote state.

        Loading a source can briefly emit Stopped. Publish the settled snapshot
        after the action, without starting audio merely to pause it again.
        """
        method = {"next_song": self._next_song, "prev_song": self._prev_song}.get(method_name)
        if method is None:
            return
        self._remote_skip_active = True
        try:
            method(playback_state)
        finally:
            self._remote_skip_active = False
            self._mpris_notify(metadata=True, playback=True)

    def toggle_shuffle(self):
        self.shuffle_mode = not self.shuffle_mode
        self.shuffle_button.setChecked(self.shuffle_mode)
        self.shuffle_button.setText("Shuffle on" if self.shuffle_mode else "Shuffle")
        self._reset_queue_navigation()
        self.status_bar.showMessage("Shuffle on — every track plays once per cycle." if self.shuffle_mode else "Shuffle off")
        self._mpris_notify(playback=True)
        self._refresh_playback_ui()

    def toggle_repeat(self):
        modes = ("off", "all", "one")
        self.repeat_mode = modes[(modes.index(self._repeat_mode()) + 1) % len(modes)]
        labels = {"off": "Repeat", "all": "Repeat all", "one": "Repeat one"}
        self.repeat_button.setText(labels[self.repeat_mode])
        self.repeat_button.setChecked(self.repeat_mode != "off")
        self.status_bar.showMessage(f"Repeat: {self.repeat_mode}")
        self._mpris_notify(playback=True)
        self._refresh_playback_ui()

    # ---------------- Volume / Mute ----------------
    def change_volume(self, value: int):
        value = max(0, min(100, int(value)))
        if USING_QT6:
            self.audio_out.setVolume(value / 100.0)
        else:
            self.player.setVolume(value)
        if self.volume_slider.value() != value:
            was_blocked = self.volume_slider.blockSignals(True)
            self.volume_slider.setValue(value)
            self.volume_slider.blockSignals(was_blocked)
        if hasattr(self, "volume_label"):
            self.volume_label.setText(f"{value}%")
        self.volume_slider.setToolTip(f"Volume: {value}%")
        self.status_bar.showMessage(f"Volume: {value}%", 2000)
        mpris = getattr(self, "mpris", None)
        if mpris:
            mpris.notify_volume()
        self._refresh_playback_ui()

    def toggle_mute(self):
        muted = self.mute_button.isChecked()
        if USING_QT6:
            self.audio_out.setMuted(muted)
        else:
            self.player.setMuted(muted)
        self.mute_button.setAccessibleName("Unmute" if muted else "Mute")
        self.mute_button.setToolTip("Unmute (M)" if muted else "Mute (M)")
        self.status_bar.showMessage("Muted" if muted else f"Volume: {self.volume_slider.value()}%", 2000)
        mpris = getattr(self, "mpris", None)
        if mpris:
            mpris.notify_volume()
        self._refresh_playback_ui()

    # ---------------- Slider / time ----------------
    def _sync_seek_enabled(self, *_args):
        self.playback_slider.setEnabled(
            not getattr(self, "_manual_stop", False)
            and self._duration_ms > 0 and self.player.isSeekable())
        self._mpris_notify(playback=True)

    def update_slider(self, position_ms: int):
        position_ms = max(0, int(position_ms))
        can_restart = position_ms > 3000
        if can_restart != getattr(self, "_desktop_can_restart", False):
            self._desktop_can_restart = can_restart
            # Previous gains/loses its restart action at this threshold. Publish
            # the capability change once, not every advancing position value.
            self._mpris_notify(playback=True)
        if position_ms != getattr(self, "_last_pos_ms", -1):
            self._last_pos_ms = position_ms
            self._last_pos_moved_at = time.monotonic()
        statuses = QMediaPlayer.MediaStatus if USING_QT6 else QMediaPlayer
        if (getattr(self, "_awaiting_repeat_restart", False)
                and 0 <= position_ms < self._duration_ms
                and self.player.mediaStatus() != statuses.EndOfMedia):
            self._awaiting_repeat_restart = False
            self._end_guard = False
        # Some backends omit EndOfMedia. Probe only after the *actual* end has
        # been reached and settled, never at duration minus one second.
        if (self._duration_ms > 0 and position_ms >= self._duration_ms
                and not self._end_guard and not getattr(self, "_manual_stop", False)):
            generation = getattr(self, "_source_generation", 0)
            if getattr(self, "_end_probe_generation", None) != generation:
                self._end_probe_generation = generation
                QtCore.QTimer.singleShot(250, lambda: self._verify_track_finished(generation))
        if self.playback_slider.isSliderDown():
            return
        sec = position_ms // 1000
        if abs(position_ms - getattr(self, "_last_slider_update_ms", -999999)) >= 200 or sec != self._last_ui_second:
            self._last_slider_update_ms = position_ms
            was_blocked = self.playback_slider.blockSignals(True)
            self.playback_slider.setValue(position_ms)
            self.playback_slider.blockSignals(was_blocked)
        if sec != self._last_ui_second:
            self._last_ui_second = sec
            self.current_time_label.setText(self._millis_to_clock(position_ms))
            self._lyrics_call("update_position", position_ms)

    def _verify_track_finished(self, generation):
        if getattr(self, "_end_probe_generation", None) == generation:
            self._end_probe_generation = None
        if (generation != getattr(self, "_source_generation", 0)
                or self._end_guard or getattr(self, "_manual_stop", False)
                or self.playback_slider.isSliderDown() or self._duration_ms <= 0
                or self.player.position() < self._duration_ms):
            return
        state = self.player.playbackState() if USING_QT6 else self.player.state()
        states = QMediaPlayer.PlaybackState if USING_QT6 else QMediaPlayer
        if state == states.PlayingState:
            self._advance_after_end()

    def set_duration(self, duration_ms: int):
        self._duration_ms = max(0, int(duration_ms))
        # A shorter duration can clamp the value and emit valueChanged. This
        # backend-driven update must not be fed back as a user seek.
        was_blocked = self.playback_slider.blockSignals(True)
        try:
            self.playback_slider.setRange(0, self._duration_ms)
        finally:
            self.playback_slider.blockSignals(was_blocked)
        live = self._duration_ms == 0 and self.current_media_type == "stream"
        self.total_time_label.setText("LIVE" if live else self._millis_to_clock(self._duration_ms))
        self._sync_seek_enabled()
        self._mpris_notify(metadata=True)

    def seek_position(self, position_ms: int):
        if not self.player.isSeekable() or self._duration_ms <= 0:
            return
        position_ms = max(0, min(self._duration_ms, int(position_ms)))
        if position_ms < self._duration_ms:
            # The user can rewind after EndOfMedia queued an advance but before
            # Qt delivers that callback. Cancel the old completion and rearm
            # detection for the next actual end of this source.
            self._source_generation = getattr(self, "_source_generation", 0) + 1
            self._end_probe_generation = None
            self._awaiting_repeat_restart = False
            self._end_guard = bool(getattr(self, "_manual_stop", False))
        self.player.setPosition(position_ms)
        self.current_time_label.setText(self._millis_to_clock(position_ms))
        self.status_bar.showMessage(f"Seek: {self._millis_to_clock(position_ms)}", 1500)
        self._lyrics_call("update_position", position_ms)
        mpris = getattr(self, "mpris", None)
        notify_seeked = getattr(mpris, "notify_seeked", None)
        if callable(notify_seeked):
            notify_seeked(position_ms)

    # ---------------- Media status / errors ----------------
    def handle_media_status(self, status):
        statuses = QMediaPlayer.MediaStatus if USING_QT6 else QMediaPlayer
        if status == statuses.EndOfMedia:
            # Ignore stale emissions after setSource() has already started the
            # next load. Qt5 enum values are integers, so string matching fails.
            if self.player.mediaStatus() == statuses.EndOfMedia:
                self._advance_after_end()
        elif status in (statuses.LoadedMedia, statuses.BufferedMedia):
            if getattr(self, "_awaiting_repeat_restart", False):
                self._awaiting_repeat_restart = False
                self._end_guard = False
            self._sync_seek_enabled()
            self._schedule_media_metadata()

    def _advance_after_end(self):
        if self._end_guard or getattr(self, "_manual_stop", False) or not self.playlist:
            return
        self._end_guard = True
        generation = getattr(self, "_source_generation", 0)
        QtCore.QTimer.singleShot(0, lambda: self._finish_end_of_media(generation))

    def _finish_end_of_media(self, generation):
        if generation != getattr(self, "_source_generation", 0) or getattr(self, "_manual_stop", False):
            return
        state = self.player.playbackState() if USING_QT6 else self.player.state()
        states = QMediaPlayer.PlaybackState if USING_QT6 else QMediaPlayer
        if state == states.PausedState:
            # Explicit Pause wins over an automatic advance already queued by
            # the end watchdog. A later genuine end can still be handled.
            self._end_guard = False
            return
        if self._repeat_mode() == "one":
            self._awaiting_repeat_restart = True
            self._last_ui_second = -1
            self.player.setPosition(0)
            self.player.play()
            self._refresh_playback_ui()
        else:
            self.next_song()

    def handle_error(self, *args):
        error = self.player.errorString()
        if not error:
            return
        self.stop_song()
        self.status_bar.showMessage(f"Unable to play: {error}. Check the source and your media codecs.")


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
    def _build_media_meta(self, file_path: str, mtype: str = 'audio') -> dict:
        """Read bounded tags, including year, with useful filename fallbacks."""
        return build_media_metadata(file_path, mtype)

    def _schedule_media_metadata(self, *_args):
        if getattr(self, "_closing", False):
            return
        self._metadata_request = getattr(self, "_play_request", None)
        self._metadata_timer.start()

    def _update_media_metadata(self):
        """Accept decoder tags only for the still-selected, loaded source."""
        if (getattr(self, "_closing", False)
                or getattr(self, "_metadata_request", None) is not getattr(self, "_play_request", None)
                or not 0 <= self.current_song_index < len(self.playlist)):
            return
        item = self.playlist[self.current_song_index]
        if item.get("id") != getattr(self, "_loaded_media_id", None):
            return
        statuses = QMediaPlayer.MediaStatus if USING_QT6 else QMediaPlayer
        if self.player.mediaStatus() in (statuses.NoMedia, statuses.LoadingMedia, statuses.InvalidMedia):
            return
        source = self.player.source() if USING_QT6 else self.player.media().canonicalUrl()
        expected = QUrl(item["path"]) if item.get("type") == "stream" else QUrl.fromLocalFile(item["path"])
        if source != expected:
            return
        changes = read_player_metadata(self.player, USING_QT6)
        if item.get("type") != "stream" and (item.get("date") or item.get("year")):
            # Some backends turn a year tag into a January 1 QDate. Retain the
            # file's original precision instead of manufacturing a full date.
            changes.pop("date", None)
            changes.pop("year", None)
        if not changes or all(item.get(field) == value for field, value in changes.items()):
            return
        if item.get("type") == "stream" and any(
                field in changes and changes[field] != item.get(field) for field in ("artist", "title")):
            # A radio station may not provide a date for its next song. Never
            # carry the previous song's year or album onto the new title.
            for field in ("album", "date", "year"):
                if field not in changes:
                    item[field] = ""
            if "title" in changes and changes["title"] != item.get("title") and "artist" not in changes:
                item["artist"] = ""
        item.update(changes)
        item["display"] = f'{item["artist"]} — {item["title"]}' if item.get("artist") else item.get("title", "")
        self._on_track_metadata_updated(item)
        self._mpris_notify(metadata=True)
        self._save_session()

    def _set_osd_mode(self, mode):
        self._osd_mode = mode if mode in {"auto", "off", "desktop", "wayland", "custom"} else "off"
        for name, action in getattr(self, "_osd_actions", {}).items():
            action.setChecked(name == self._osd_mode)
        retry_action = getattr(self, "_osd_retry_action", None)
        if retry_action is not None:
            retry_action.setEnabled(self._osd_mode != "off")
        backend_action = getattr(self, "_osd_backend_action", None)
        if backend_action is not None:
            backend_action.setText("Notifications are off" if self._osd_mode == "off" else "Waiting for a song notification")
            backend_action.setToolTip("")
        osd = getattr(self, "osd", None)
        if osd is not None:
            osd.set_mode(self._osd_mode)
            if self._osd_mode != "off":
                self._osd_notify()
            message = {
                "auto": "Automatic song notifications enabled · available services will be detected.",
                "off": "Song notifications are off. Desktop media controls remain available.",
                "desktop": "Desktop notifications enabled · finding your notification service.",
                "wayland": "Wayland OSD enabled · finding a compatible service or installed client.",
                "custom": "Custom notifications enabled · using your configured command adapter.",
            }[self._osd_mode]
            self.status_bar.showMessage(message, 8000)
        self._save_session()

    def _on_osd_selected(self, backend):
        """Show the service confirmed by the adapter, not a desktop-name guess."""
        description = safe_metadata_text(backend, 180)
        if not description or getattr(self, "_closing", False):
            return
        action = getattr(self, "_osd_backend_action", None)
        if action is not None:
            action.setText(("Using: " + description).replace("&", "&&"))
            action.setToolTip(description)
        self.status_bar.showMessage("Song notifications: " + description, 6000)

    def _on_osd_unavailable(self, message):
        if getattr(self, "_closing", False):
            return
        action = getattr(self, "_osd_backend_action", None)
        if action is not None:
            action.setText("Notification service unavailable · retry after setup")
            action.setToolTip(safe_metadata_text(message, 500))
        self.status_bar.showMessage(message, 8000)

    def _osd_notify(self):
        osd = getattr(self, "osd", None)
        if osd is None or getattr(self, "_closing", False) or getattr(self, "_restoring_session", False):
            return
        item = self.playlist[self.current_song_index] if 0 <= self.current_song_index < len(self.playlist) else None
        osd.update(item, self._desktop_playback_status())

    def _mpris_notify(self, metadata: bool = False, playback: bool = False):
        """Publish one status snapshot, including track-derived capabilities."""
        if getattr(self, "_closing", False) or getattr(self, "_remote_skip_active", False):
            return
        try:
            self._osd_notify()
        except Exception:
            # An unavailable notification service never prevents media control.
            pass
        m = getattr(self, 'mpris', None)
        if not m:
            return
        metadata_sent = False
        if metadata:
            try:
                fn = getattr(m, 'notify_metadata', None)
                if callable(fn):
                    fn()
                    # notify_metadata also refreshes playback capabilities.
                    # Sending them again duplicates both property construction
                    # and desktop-bus traffic on every load and queue edit.
                    metadata_sent = True
            except Exception:
                # Metadata encoding can fail independently of valid playback
                # properties. Preserve the explicitly requested status update.
                pass
        if playback and not metadata_sent:
            try:
                fn = getattr(m, 'notify_playback', None)
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
                self.resize(max(1180, self.width()), self.height())
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
        from generic_player_library import MAX_PLAYLIST_ENTRIES, normalize_entry, validate_stream_url
        name = self.custom_station_name.text().strip()
        try:
            url = validate_stream_url(self.custom_station_url.text())
            item = normalize_entry({"path": url, "type": "stream", "title": name, "display": name})
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid stream URL", str(exc))
            self.custom_station_url.setFocus()
            return
        if any(entry.get("path") == url for entry in self.playlist):
            self.status_bar.showMessage("This stream is already in your queue.", 4000)
            return
        if len(self.playlist) >= MAX_PLAYLIST_ENTRIES:
            self.status_bar.showMessage(
                f"Queue limit of {MAX_PLAYLIST_ENTRIES:,} tracks reached. Remove tracks before adding another stream.", 6000)
            return
        self.playlist.append(item)
        self._queue_changed()
        self.custom_station_name.clear()
        self.custom_station_url.clear()
        self.status_bar.showMessage(f"Added stream: {item['title']}", 4000)

    # ---------------- State change adapters ----------------
    def _on_playback_state_changed(self, state):
        """Translate Qt6's signal; preserve a backend-query fallback on Qt5."""
        playing = False
        try:
            if USING_QT6:
                playing = (state == QMediaPlayer.PlaybackState.PlayingState)
            else:
                playing = bool(self.player_is_playing())
        except Exception:
            playing = bool(getattr(self, "_last_playing", False))
        self._apply_playing_state(playing)

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
        self._apply_playing_state(playing)

    def _apply_playing_state(self, playing):
        """Keep both Qt signal adapters on the same UI/lyrics/desktop path."""
        states = QMediaPlayer.PlaybackState if USING_QT6 else QMediaPlayer
        state = self.player.playbackState() if USING_QT6 else self.player.state()
        if state in (states.PlayingState, states.PausedState):
            self._pending_playback_state = None
            self._pending_playback_request = None
        self._last_playing = playing

        self._set_play_button_text("Pause" if playing else "Play")
        self._on_playback_ui_changed()
        self._lyrics_call("set_playing", playing)
        self._update_controls_enabled()

        self._mpris_notify(playback=True)

    def closeEvent(self, event):
        """Stop timers, playback, and helper modules cleanly."""
        if self._closing:
            super().closeEvent(event)
            return
        self._save_session(immediate=True)
        self._closing = True
        # Invalidate deferred end callbacks before stop() emits backend signals.
        self._manual_stop = True
        self._end_guard = True
        self._source_generation = getattr(self, "_source_generation", 0) + 1
        for name in ("_session_timer", "_sleep_timer", "_session_save_timer", "_metadata_timer"):
            timer = getattr(self, name, None)
            if timer:
                timer.stop()
        application = QtWidgets.QApplication.instance()
        if application:
            application.removeEventFilter(self)
        # Cancel optional notification work before decoder teardown emits state.
        try:
            if self.osd is not None:
                self.osd.shutdown()
        except Exception:
            pass

        # Release decoder workers while their output widgets still exist.
        # A deleted or failing backend object must not skip the independent
        # visualizer/output cleanup that follows it. Resolve each action only
        # when it runs, so an unavailable attribute is isolated as well.
        for release in (
            lambda: stop_media_player(self.player),
            lambda: self.player.setSource(QUrl()) if USING_QT6 else self.player.setMedia(QMediaContent()),
            lambda: self.visualizer.shutdown(),
            lambda: self.player.setVideoOutput(None),
            lambda: self.player.setAudioOutput(None) if USING_QT6 else None,
        ):
            try:
                release()
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
def main(argv=None):
    """Console entry point; optional filenames/stream URLs are added to the queue."""
    arguments = _script_arguments if argv is None and _script_arguments is not None else _parse_command_line(
        sys.argv[1:] if argv is None else argv)
    # Parsed source arguments, including filenames starting with '-', belong
    # to the importer and must not be interpreted as Qt command-line options.
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Generic Player")
    app.setOrganizationName("GenericPlayer")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("generic-player")
    app.setStyle("Fusion")
    window = MainWindow()
    if arguments.sources:
        window._process_dropped_files(arguments.sources)
    window.show()
    return app.exec() if USING_QT6 else app.exec_()


if __name__ == "__main__":
    sys.exit(main())
