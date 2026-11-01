"""Publish desktop controls through the application's existing Qt event loop.

The bridge observes the window's playback state. It does not start GLib, a
polling worker, playerctl, or an OSD process. D-Bus uses microseconds and typed
variants while the playback controller uses milliseconds. Missing QtDBus or
an unavailable session bus only disables desktop media controls.
"""

import hashlib
import importlib
import logging
import math
import os
import re
import uuid

from generic_player_qt import QtCore, QtMultimedia, USING_QT6, stop_media_player
from generic_player_metadata import normalize_media_date, normalize_year, safe_metadata_text

QUrl = QtCore.QUrl
QMediaPlayer = QtMultimedia.QMediaPlayer
_LOG = logging.getLogger(__name__)
_MPRIS_IMPORT_ERROR = None
try:
    QtDBus = importlib.import_module(f"PyQt{'6' if USING_QT6 else '5'}.QtDBus")
except (ImportError, RuntimeError) as error:
    QtDBus = None
    _MPRIS_IMPORT_ERROR = error

ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = ROOT_IFACE + ".Player"
MAX_TIME_MS = (2 ** 63 - 1) // 1000


def _typed_variant(value, type_name):
    """Keep int64 and string-array signatures, even for an empty array."""
    variant = QtCore.QVariant(value)
    if USING_QT6:
        target = QtCore.QMetaType(getattr(QtCore.QMetaType.Type, type_name).value)
    else:
        target = getattr(QtCore.QVariant, "StringList" if type_name == "QStringList" else type_name)
    if not variant.convert(target):
        raise ValueError(f"Cannot encode MPRIS value as {type_name}.")
    return variant


def _property(name, value_type, default, *, writable=False):
    """Expose a bridge property without letting exceptions cross into Qt."""
    def read(adaptor):
        try:
            return getattr(adaptor.bridge, name)
        except Exception:
            _LOG.debug("Could not read MPRIS property %s", name, exc_info=True)
            return default

    def write(adaptor, value):
        try:
            setattr(adaptor.bridge, name, value)
        except Exception:
            _LOG.debug("Could not set MPRIS property %s", name, exc_info=True)

    return QtCore.pyqtProperty(value_type, fget=read, fset=write if writable else None)


def _interface(name):
    # Qt6 requires a class decorator; Qt5 records Q_CLASSINFO in the class
    # body instead. Applying the wrong form silently loses the D-Bus name.
    if USING_QT6:
        return QtCore.pyqtClassInfo("D-Bus Interface", name)
    return lambda cls: cls


if QtDBus is not None:
    class _Adaptor(QtDBus.QDBusAbstractAdaptor):
        def __init__(self, bridge, parent):
            super().__init__(parent)
            self.bridge = bridge
            self.setAutoRelaySignals(False)

        def dispatch(self, method, *arguments):
            # Backend failures must not abort the application inside PyQt.
            try:
                getattr(self.bridge, method)(*arguments)
            except Exception:
                _LOG.debug("MPRIS method %s failed", method, exc_info=True)

    @_interface(ROOT_IFACE)
    class _RootAdaptor(_Adaptor):
        if not USING_QT6:
            QtCore.Q_CLASSINFO("D-Bus Interface", ROOT_IFACE)
        CanQuit = _property("CanQuit", bool, True)
        CanRaise = _property("CanRaise", bool, True)
        HasTrackList = _property("HasTrackList", bool, False)
        Identity = _property("Identity", str, "Generic Player")
        DesktopEntry = _property("DesktopEntry", str, "generic-player")
        SupportedUriSchemes = _property("SupportedUriSchemes", "QStringList", [])
        SupportedMimeTypes = _property("SupportedMimeTypes", "QStringList", [])

        @QtCore.pyqtSlot()
        def Raise(self):
            self.dispatch("Raise")

        @QtCore.pyqtSlot()
        def Quit(self):
            # Reply before the close hook unregisters this D-Bus object.
            QtCore.QTimer.singleShot(0, lambda: self.dispatch("Quit"))

    @_interface(PLAYER_IFACE)
    class _PlayerAdaptor(_Adaptor):
        if not USING_QT6:
            QtCore.Q_CLASSINFO("D-Bus Interface", PLAYER_IFACE)
        PlaybackStatus = _property("PlaybackStatus", str, "Stopped")
        LoopStatus = _property("LoopStatus", str, "None", writable=True)
        Rate = _property("Rate", float, 1.0, writable=True)
        Shuffle = _property("Shuffle", bool, False, writable=True)
        Metadata = _property("Metadata", "QVariantMap", {})
        Volume = _property("Volume", float, 0.0, writable=True)
        Position = _property("Position", "qlonglong", 0)
        MinimumRate = _property("MinimumRate", float, 0.5)
        MaximumRate = _property("MaximumRate", float, 2.0)
        CanGoNext = _property("CanGoNext", bool, False)
        CanGoPrevious = _property("CanGoPrevious", bool, False)
        CanPlay = _property("CanPlay", bool, False)
        CanPause = _property("CanPause", bool, False)
        CanSeek = _property("CanSeek", bool, False)
        CanControl = _property("CanControl", bool, True)
        Seeked = QtCore.pyqtSignal("qlonglong")

        @QtCore.pyqtSlot()
        def Play(self):
            self.dispatch("Play")

        @QtCore.pyqtSlot()
        def Pause(self):
            self.dispatch("Pause")

        @QtCore.pyqtSlot()
        def PlayPause(self):
            self.dispatch("PlayPause")

        @QtCore.pyqtSlot()
        def Stop(self):
            self.dispatch("Stop")

        @QtCore.pyqtSlot()
        def Next(self):
            self.dispatch("Next")

        @QtCore.pyqtSlot()
        def Previous(self):
            self.dispatch("Previous")

        @QtCore.pyqtSlot("qlonglong")
        def Seek(self, offset):
            self.dispatch("Seek", offset)

        @QtCore.pyqtSlot(QtDBus.QDBusObjectPath, "qlonglong")
        def SetPosition(self, track_id, position):
            self.dispatch("SetPosition", track_id.path(), position)

        @QtCore.pyqtSlot(str)
        def OpenUri(self, uri):
            self.dispatch("OpenUri", uri)


class GenericPlayerMPRIS:
    """Expose actual playback state, typed metadata, and canonical controls."""

    OBJ_PATH = "/org/mpris/MediaPlayer2"
    PLAYER_IFACE = PLAYER_IFACE
    NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"
    CanQuit = True
    CanRaise = True
    HasTrackList = False
    CanControl = True
    Identity = "Generic Player"
    DesktopEntry = "generic-player"
    SupportedUriSchemes = ["file", "http", "https"]
    SupportedMimeTypes = [
        "audio/mpeg", "audio/flac", "audio/ogg", "audio/wav", "audio/mp4",
        "audio/aac", "audio/opus", "audio/x-ms-wma", "video/mp4",
        "video/x-matroska", "video/webm", "video/x-msvideo", "video/quicktime",
    ]
    MinimumRate = 0.5
    MaximumRate = 2.0

    def __init__(self, app, name="Generic_Player"):
        if QtDBus is None:
            raise RuntimeError("QtDBus is unavailable; desktop media controls are disabled.") from _MPRIS_IMPORT_ERROR
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
            raise ValueError("Invalid MPRIS player name.")
        self.app = app
        self.bus = None
        self.bus_name = ""
        self._object = None
        self._root_adaptor = None
        self._player_adaptor = None
        self._registered_object = False
        self._last_properties = {}
        self._connection_name = f"generic-player-mpris-{os.getpid()}-{uuid.uuid4().hex}"
        bus_type = QtDBus.QDBusConnection.BusType.SessionBus if USING_QT6 else QtDBus.QDBusConnection.SessionBus
        # A private Qt connection gives this bridge exclusive ownership of its
        # registered objects, including when two windows share one process.
        self.bus = QtDBus.QDBusConnection.connectToBus(bus_type, self._connection_name)
        try:
            if not self.bus.isConnected():
                raise RuntimeError("No D-Bus session is available for desktop media controls.")
            parent = app if isinstance(app, QtCore.QObject) else None
            self._object = QtCore.QObject(parent)
            self._root_adaptor = _RootAdaptor(self, self._object)
            self._player_adaptor = _PlayerAdaptor(self, self._object)
            if not self.bus.registerObject(self.OBJ_PATH, self._object):
                raise RuntimeError(self.bus.lastError().message())
            self._registered_object = True
            primary = f"{ROOT_IFACE}.{name}"
            for candidate in (primary, f"{primary}.instance{os.getpid()}"):
                if self.bus.registerService(candidate):
                    self.bus_name = candidate
                    break
            if not self.bus_name:
                raise RuntimeError("Cannot claim an MPRIS service name: " + self.bus.lastError().message())
        except Exception:
            self.stop()
            raise

    def start(self, interval_ms=30):
        """Compatibility hook: Qt dispatches D-Bus without a polling timer."""
        self.notify_metadata()
        self.notify_volume()

    def poll(self):
        """Retained for embedders of the former GLib bridge; no work needed."""

    def stop(self):
        """Release only this bridge's service, object and private connection."""
        bus = self.bus
        self.bus = None
        if bus is not None:
            if self._registered_object:
                bus.unregisterObject(self.OBJ_PATH)
        self.bus_name = ""
        self._registered_object = False
        if self._connection_name:
            # Closing the dedicated connection also releases its service name.
            # Do not synchronously wait for ReleaseName during shutdown: the
            # Qt event loop may already have stopped dispatching bus replies.
            QtDBus.QDBusConnection.disconnectFromBus(self._connection_name)
            self._connection_name = ""
        if self._object is not None:
            self._object.deleteLater()
        self._object = None
        self._root_adaptor = None
        self._player_adaptor = None
        self._last_properties.clear()

    def Raise(self):
        self.app.show()
        if "wayland" not in os.environ.get("XDG_SESSION_TYPE", "").lower():
            self.app.raise_()
            self.app.activateWindow()

    def Quit(self):
        self.app.close()

    def Play(self):
        # Explicit commands must not toggle, and must not be lost to the
        # debounce guard used for duplicate physical PlayPause key events.
        if not self.CanPlay or self._actual_status() == "Playing":
            return
        if self._actual_status() == "Paused":
            self.app._manual_stop = False
            if not self._call_app("_resume_playback"):
                self.app.player.play()
        else:
            if not self._current_item():
                self.app.current_song_index = 0
                widget = getattr(self.app, "playlist_widget", None)
                if widget is not None:
                    widget.setCurrentRow(0)
            self._call_app("play_song")
        self.notify_playback()

    def Pause(self):
        if self._actual_status() == "Playing":
            if not self._call_app("_pause_playback"):
                self.app.player.pause()
            self.notify_playback()

    def PlayPause(self):
        # Every remote call is a command; debounce only physical key events.
        if self._actual_status() == "Playing":
            self.Pause()
        else:
            self.Play()

    def Stop(self):
        self._call_app("stop_song")
        self.notify_playback()

    def _skip(self, method):
        previous_status = self._actual_status()
        skip = getattr(self.app, "_skip_from_remote", None)
        if callable(skip):
            skip(method, previous_status)
        else:
            # Compatibility for embedders without the state-preserving load
            # hook. Generic Player itself never briefly plays a paused skip.
            self._call_app(method)
            if previous_status == "Paused":
                self.app.player.pause()
            elif previous_status == "Stopped" and not self._call_app("stop_song"):
                stop_media_player(self.app.player)
        self.notify_metadata()

    def Next(self):
        if self.CanGoNext:
            self._skip("next_song")

    def Previous(self):
        if self.CanGoPrevious:
            self._skip("prev_song")

    def Seek(self, offset):
        if not self.CanSeek:
            return
        target_us = self.Position + int(offset)
        if target_us > self._duration_ms() * 1000:
            self.Next()
            return
        self._seek_ms(max(0, target_us // 1000))

    def SetPosition(self, track_id, position):
        position = int(position)
        if (not self.CanSeek or track_id != self._track_id()
                or position < 0 or position > self._duration_ms() * 1000):
            return
        self._seek_ms(position // 1000)

    def OpenUri(self, uri):
        from pathlib import Path
        from generic_player_library import decode_local_file_url, media_type, validate_stream_url

        raw_uri = str(uri)
        url = QUrl(raw_uri)
        if not url.isValid() or url.scheme().lower() not in self.SupportedUriSchemes:
            return
        if url.isLocalFile():
            try:
                # Validate the original URI before QUrl normalizes authorities,
                # drops query/fragment text or replaces invalid UTF-8 bytes.
                path = str(Path(decode_local_file_url(raw_uri)).resolve())
            except (OSError, RuntimeError, ValueError):
                # Unavailable paths and symlink loops are invalid URI inputs,
                # not failures of the desktop-control service.
                return
            if media_type(path) is None:
                return
        else:
            try:
                path = validate_stream_url(raw_uri)
            except ValueError:
                return
        add = getattr(self.app, "_process_dropped_files", None)
        if callable(add):
            add([path])
        for index, item in enumerate(getattr(self.app, "playlist", [])):
            if item.get("path") == path:
                self.app.current_song_index = index
                self._call_app("play_song")
                return

    def force_playback_status(self, status, timeout_ms=450):
        """Compatibility hook; publish actual state instead of a stale hint."""
        self.notify_playback()

    def clear_forced_playback_status(self):
        """Compatibility hook; the Qt bridge never overrides backend state."""

    @property
    def PlaybackStatus(self):
        return self._actual_status()

    @property
    def Position(self):
        if not self._current_item():
            return 0
        player = getattr(self.app, "player", None)
        return min(MAX_TIME_MS, max(0, int(player.position()))) * 1000 if player else 0

    @property
    def LoopStatus(self):
        mode = getattr(self.app, "repeat_mode", "off")
        return {"off": "None", "all": "Playlist", "one": "Track", True: "Track"}.get(mode, "None")

    @LoopStatus.setter
    def LoopStatus(self, value):
        modes = {"None": "off", "Playlist": "all", "Track": "one"}
        if value not in modes:
            return
        # Use the same UI action so labels, saved state and queue history agree.
        target = modes[value]
        for _ in range(3):
            if getattr(self.app, "repeat_mode", "off") == target:
                break
            if not self._call_app("toggle_repeat"):
                self.app.repeat_mode = target
                break
        self.notify_playback()

    @property
    def Shuffle(self):
        return bool(getattr(self.app, "shuffle_mode", False))

    @Shuffle.setter
    def Shuffle(self, value):
        if bool(value) != self.Shuffle:
            if not self._call_app("toggle_shuffle"):
                self.app.shuffle_mode = bool(value)
            self.notify_playback()

    @property
    def Rate(self):
        player = getattr(self.app, "player", None)
        rate = float(player.playbackRate()) if player else 1.0
        return rate if math.isfinite(rate) else 1.0

    @Rate.setter
    def Rate(self, value):
        value = float(value)
        if not math.isfinite(value):
            return
        if value == 0:
            self.Pause()
            return
        if not self.MinimumRate <= value <= self.MaximumRate:
            return
        combo = getattr(self.app, "rate_combo", None)
        if combo is not None and combo.count():
            # Snap to the supported choices, keeping the displayed rate honest.
            index = min(range(combo.count()), key=lambda i: abs(float(combo.itemData(i)) - value))
            combo.setCurrentIndex(index)
            value = float(combo.itemData(index))
        self.app.player.setPlaybackRate(value)
        self.notify_playback()

    @property
    def Metadata(self):
        item = self._current_item()
        if item is None or QtDBus is None:
            return {}
        path = item.get("path", "")
        title = item.get("title") or item.get("display") or os.path.basename(path) or "Unknown track"
        artist = item.get("artist")
        if isinstance(artist, str):
            artist = [artist] if artist else []
        elif not isinstance(artist, (list, tuple)):
            artist = []
        artists = [safe_metadata_text(name) for name in artist[:64] if isinstance(name, str)]
        metadata = {
            "mpris:trackid": QtDBus.QDBusObjectPath(self._track_id()),
            "xesam:title": safe_metadata_text(title) or "Unknown track",
            "xesam:artist": _typed_variant([name for name in artists if name], "QStringList"),
        }
        if path:
            url = QUrl(path) if item.get("type") == "stream" else QUrl.fromLocalFile(os.path.abspath(path))
            metadata["xesam:url"] = safe_metadata_text(url.toString(), limit=32768)
        if item.get("album"):
            metadata["xesam:album"] = safe_metadata_text(item["album"])
        date = normalize_media_date(item.get("date"))
        year = normalize_year(item.get("year")) or normalize_year(date)
        if date or year:
            # ISO 8601 permits reduced precision: retain a year-only tag
            # without inventing a month or day that was never in the file.
            metadata["xesam:contentCreated"] = date or year
        if year:
            metadata["generic-player:year"] = year
        duration = self._duration_ms()
        if duration > 0:
            metadata["mpris:length"] = _typed_variant(duration * 1000, "LongLong")
        return metadata

    @property
    def Volume(self):
        audio = getattr(self.app, "audio_out", None)
        if audio is not None:
            return 0.0 if audio.isMuted() else float(audio.volume())
        player = getattr(self.app, "player", None)
        if player is not None and hasattr(player, "volume"):
            return 0.0 if player.isMuted() else float(player.volume()) / 100.0
        slider = getattr(self.app, "volume_slider", None)
        return float(slider.value()) / 100.0 if slider is not None else 0.7

    @Volume.setter
    def Volume(self, value):
        value = float(value)
        if not math.isfinite(value):
            return
        value = max(0.0, min(1.0, value))
        slider = getattr(self.app, "volume_slider", None)
        audio = getattr(self.app, "audio_out", None)
        player = getattr(self.app, "player", None)
        if slider is not None:
            slider.setValue(round(value * 100))
        elif audio is not None:
            audio.setVolume(value)
        elif player is not None:
            player.setVolume(round(value * 100))
        if value > 0:
            target = audio if audio is not None else player
            mute_button = getattr(self.app, "mute_button", None)
            handled = False
            if mute_button is not None:
                mute_button.setChecked(False)
                # Use the normal UI action even when the slider value did not
                # change, so the icon, tooltip, accessible name and saved state
                # agree with the unmuted audio output.
                handled = self._call_app("toggle_mute")
                if not handled:
                    mute_button.setText("Mute")
            if not handled and target is not None:
                target.setMuted(False)
        self.notify_volume()

    @property
    def CanGoNext(self):
        playlist = getattr(self.app, "playlist", [])
        index = getattr(self.app, "current_song_index", -1)
        return bool(playlist) and (index + 1 < len(playlist)
                                  or self.LoopStatus == "Playlist"
                                  or (self.Shuffle and len(playlist) > 1))

    @property
    def CanGoPrevious(self):
        playlist = getattr(self.app, "playlist", [])
        index = getattr(self.app, "current_song_index", -1)
        return bool(playlist) and (index > 0 or self.LoopStatus == "Playlist"
                                  or (self.Shuffle and len(playlist) > 1)
                                  or (self.CanSeek and self.Position > 3000000))

    @property
    def CanPlay(self):
        return bool(getattr(self.app, "playlist", []))

    @property
    def CanPause(self):
        return self._current_item() is not None

    @property
    def CanSeek(self):
        player = getattr(self.app, "player", None)
        return (self._current_item() is not None and player is not None
                and bool(player.isSeekable()) and self._duration_ms() > 0)

    @staticmethod
    def _plain(value):
        """Small immutable snapshots avoid duplicate signals and QVariant quirks."""
        if isinstance(value, QtCore.QVariant):
            return GenericPlayerMPRIS._plain(value.value())
        if isinstance(value, QtDBus.QDBusObjectPath):
            return value.path()
        if isinstance(value, dict):
            return tuple((key, GenericPlayerMPRIS._plain(item)) for key, item in sorted(value.items()))
        if isinstance(value, (list, tuple)):
            return tuple(GenericPlayerMPRIS._plain(item) for item in value)
        return value

    def _emit_properties_changed(self, values):
        bus = self.bus
        if bus is None or not bus.isConnected() or not self.bus_name:
            return
        changed, snapshots = {}, {}
        for name, value in values.items():
            snapshot = self._plain(value)
            if name not in self._last_properties or self._last_properties[name] != snapshot:
                changed[name] = value
                snapshots[name] = snapshot
        if not changed:
            return
        message = QtDBus.QDBusMessage.createSignal(
            self.OBJ_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged")
        message.setArguments([self.PLAYER_IFACE, changed, _typed_variant([], "QStringList")])
        if bus.send(message):
            # An unavailable bus must not mark unsent values as delivered.
            self._last_properties.update(snapshots)

    def _playback_values(self):
        return {name: getattr(self, name) for name in (
            "PlaybackStatus", "LoopStatus", "Shuffle", "Rate", "CanPlay",
            "CanPause", "CanSeek", "CanGoNext", "CanGoPrevious",
        )}

    def notify_playback(self):
        self._emit_properties_changed(self._playback_values())

    def notify_metadata(self):
        """Publish track data and dependent controls as one changed snapshot."""
        values = self._playback_values()
        values["Metadata"] = self.Metadata
        self._emit_properties_changed(values)

    def notify_volume(self):
        self._emit_properties_changed({"Volume": self.Volume})

    def notify_seeked(self, position_ms):
        if self.bus is not None and self._player_adaptor is not None:
            self._player_adaptor.Seeked.emit(min(MAX_TIME_MS, max(0, int(position_ms))) * 1000)

    def _seek_ms(self, position_ms):
        seek = getattr(self.app, "seek_position", None)
        if callable(seek):
            seek(position_ms)
        else:
            self.app.player.setPosition(position_ms)
            self.notify_seeked(position_ms)

    def _current_item(self):
        index = getattr(self.app, "current_song_index", -1)
        playlist = getattr(self.app, "playlist", [])
        if isinstance(index, int) and 0 <= index < len(playlist):
            item = playlist[index]
            if isinstance(item, dict):
                return item
        return None

    def _track_id(self):
        item = self._current_item()
        if item is None:
            return self.NO_TRACK
        # Include the entry ID as well as its source: repeated appearances of
        # the same file are different tracks, but reordering keeps identity.
        identity = (str(item.get("id", "")) + "\0" + str(item.get("type", "audio"))
                    + "\0" + str(item.get("path", "")))
        digest = hashlib.sha256(identity.encode("utf-8", "surrogatepass")).hexdigest()[:24]
        return f"/org/generic_player/track/{digest}"

    def _duration_ms(self):
        duration = getattr(self.app, "_duration_ms", 0)
        if not duration:
            duration = (self._current_item() or {}).get("duration_ms", 0)
        return min(MAX_TIME_MS, max(0, int(duration)))

    def _actual_status(self):
        status = getattr(self.app, "_desktop_playback_status", None)
        if callable(status):
            value = status()
            if value in ("Playing", "Paused", "Stopped"):
                return value
        player = getattr(self.app, "player", None)
        if player is None:
            return "Stopped"
        state = player.playbackState() if USING_QT6 else player.state()
        states = QMediaPlayer.PlaybackState if USING_QT6 else QMediaPlayer.State
        return {states.PlayingState: "Playing", states.PausedState: "Paused"}.get(state, "Stopped")

    def _call_app(self, method_name):
        fn = getattr(self.app, method_name, None)
        if callable(fn):
            fn()
            return True
        return False

    def _is_playing(self):
        return self._actual_status() == "Playing"
