# generic_player_mpris.py
# ------------------------------------------------------------
# Generic Player - MPRIS2 / playerctl support (PyQt5 + PyQt6)
#
# Features:
#   - Correct object path: /org/mpris/MediaPlayer2 (playerctl/DE requirement)
#   - Proper a{sv} Metadata values (GLib.Variant) to avoid GetAll() TypeError
#   - Metadata for local files so desktop shows track info
#   - Non-blocking GLib integration via poll() (Qt drives via QTimer)
#   - Optional PropertiesChanged helpers for snappy OSD updates
# ------------------------------------------------------------

import os
import time

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from pydbus import SessionBus

try:
    from PyQt6.QtCore import QUrl, QTimer
    from PyQt6.QtMultimedia import QMediaPlayer
    USING_QT6 = True
except Exception:
    from PyQt5.QtCore import QUrl, QTimer
    from PyQt5.QtMultimedia import QMediaPlayer
    USING_QT6 = False


class GenericPlayerMPRIS:
    """MPRIS2 bridge for Generic Player."""

    # Introspection XML for pydbus.
    dbus = """
    <node>
      <interface name='org.mpris.MediaPlayer2'>
        <method name='Raise'/>
        <method name='Quit'/>
        <property name='CanQuit' type='b' access='read'/>
        <property name='CanRaise' type='b' access='read'/>
        <property name='HasTrackList' type='b' access='read'/>
        <property name='Identity' type='s' access='read'/>
        <property name='DesktopEntry' type='s' access='read'/>
        <property name='SupportedUriSchemes' type='as' access='read'/>
        <property name='SupportedMimeTypes' type='as' access='read'/>
      </interface>

      <interface name='org.mpris.MediaPlayer2.Player'>
        <method name='Play'/>
        <method name='Pause'/>
        <method name='PlayPause'/>
        <method name='Stop'/>
        <method name='Next'/>
        <method name='Previous'/>
        <method name='Seek'>
          <arg type='x' name='Offset' direction='in'/>
        </method>

        <property name='PlaybackStatus' type='s' access='read'/>
        <property name='Metadata' type='a{sv}' access='read'/>
        <property name='Volume' type='d' access='readwrite'/>
        <property name='CanGoNext' type='b' access='read'/>
        <property name='CanGoPrevious' type='b' access='read'/>
        <property name='CanPlay' type='b' access='read'/>
        <property name='CanPause' type='b' access='read'/>
        <property name='CanSeek' type='b' access='read'/>
        <property name='CanControl' type='b' access='read'/>
      </interface>
    </node>
    """

    OBJ_PATH = "/org/mpris/MediaPlayer2"
    PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"

    def __init__(self, app, name: str = "Generic_Player"):
        self.app = app
        self.bus = SessionBus()

        # Forced status (for instant OSD response). Cleared on real Qt state change.
        self._forced_playback_status = None
        self._forced_until = 0.0

        # Publish with explicit object path.
        self.bus.publish(
            f"org.mpris.MediaPlayer2.{name}",
            (self.OBJ_PATH, self),
        )

        # Non-blocking GLib integration (Qt drives poll() via QTimer)
        self._glib_context = GLib.MainContext.default()

    def start(self, interval_ms: int = 30):
        """Start Qt-driven polling (backward compatibility).

        Generic Player can either drive :meth:`poll` itself (recommended) or call this.
        """
        try:
            self._qt_timer = QTimer(self.app)
            self._qt_timer.timeout.connect(self.poll)
            self._qt_timer.start(int(interval_ms))
        except Exception:
            pass

    def poll(self):
        """Pump DBus without blocking Qt."""
        try:
            while self._glib_context.pending():
                self._glib_context.iteration(False)
        except Exception:
            pass

    def stop(self):
        try:
            if hasattr(self, "_qt_timer") and self._qt_timer is not None:
                self._qt_timer.stop()
        except Exception:
            pass
        return

# ---------------- org.mpris.MediaPlayer2 ----------------
    def Raise(self):
        try:
            self.app.show()
            if hasattr(self.app, "raise_"):
                self.app.raise_()
            if hasattr(self.app, "activateWindow"):
                self.app.activateWindow()
        except Exception:
            pass

    def Quit(self):
        try:
            self.app.close()
        except Exception:
            pass

    CanQuit = True
    CanRaise = True
    HasTrackList = False
    Identity = "Generic Player"
    DesktopEntry = "Generic_Player"
    SupportedUriSchemes = ["file", "http", "https"]
    SupportedMimeTypes = [
        "audio/mpeg", "audio/flac", "audio/ogg", "audio/wav",
        "video/mp4", "video/x-matroska",
    ]

    # ---------------- org.mpris.MediaPlayer2.Player methods ----------------
    def Play(self):
        # Play must not toggle
        if not self._is_playing():
            # Force first so DE OSD shows the *new* state immediately.
            self.force_playback_status("Playing")
            self._call_app("play_pause_song")

    def Pause(self):
        # Pause must not toggle
        if self._is_playing():
            # Force first so DE OSD shows the *new* state immediately.
            self.force_playback_status("Paused")
            self._call_app("play_pause_song")

    def PlayPause(self):
        # Force first so DE OSD shows the *new* state immediately.
        try:
            self.force_playback_status("Paused" if self._is_playing() else "Playing")
        except Exception:
            pass
        self._call_app("play_pause_song")

    def Stop(self):
        self._call_app("stop_song")

    def Next(self):
        self._call_app("next_song")

    def Previous(self):
        self._call_app("prev_song")

    def Seek(self, offset):
        # offset is microseconds; QMediaPlayer uses milliseconds
        try:
            if not self.CanSeek:
                return
            new_pos_ms = int(self.app.player.position() + (int(offset) / 1000))
            if new_pos_ms < 0:
                new_pos_ms = 0
            self.app.player.setPosition(new_pos_ms)
        except Exception:
            pass

    # ---------------- Forced playback status (for instant OSD) ----------------
    def force_playback_status(self, status: str, timeout_ms: int = 450):
        """Temporarily override PlaybackStatus to make DE OSD react instantly."""
        try:
            s = str(status)
            if s not in ("Playing", "Paused", "Stopped"):
                return
            self._forced_playback_status = s
            self._forced_until = time.monotonic() + max(0.0, float(timeout_ms) / 1000.0)
            self.notify_playback()
        except Exception:
            pass

    def clear_forced_playback_status(self):
        try:
            self._forced_playback_status = None
            self._forced_until = 0.0
        except Exception:
            pass

    # ---------------- Properties ----------------
    @property
    def PlaybackStatus(self):
        try:
            if self._forced_playback_status and time.monotonic() < float(self._forced_until):
                return str(self._forced_playback_status)
        except Exception:
            pass

        try:
            if USING_QT6:
                state = self.app.player.playbackState()
                return {
                    QMediaPlayer.PlaybackState.PlayingState: "Playing",
                    QMediaPlayer.PlaybackState.PausedState: "Paused",
                }.get(state, "Stopped")
            else:
                state = self.app.player.state()
                return {
                    QMediaPlayer.State.PlayingState: "Playing",
                    QMediaPlayer.State.PausedState: "Paused",
                }.get(state, "Stopped")
        except Exception:
            return "Stopped"

    @property
    def Metadata(self):
        """Return MPRIS Metadata as a{sv}.

        IMPORTANT: For the `a{sv}` signature, each dict value must be a
        GLib.Variant (because value type is 'v').
        """

        def V(sig: str, val):
            return GLib.Variant(sig, val)

        def pack(md_plain: dict) -> dict:
            md: dict = {}
            for k, v in (md_plain or {}).items():
                try:
                    if k == "mpris:trackid":
                        md[k] = V("o", str(v))
                    elif k in ("xesam:title", "xesam:url", "xesam:album"):
                        md[k] = V("s", "" if v is None else str(v))
                    elif k == "xesam:artist":
                        if v is None:
                            md[k] = V("as", [])
                        elif isinstance(v, (list, tuple)):
                            md[k] = V("as", [str(x) for x in v])
                        else:
                            md[k] = V("as", [str(v)])
                    elif k == "mpris:length":
                        md[k] = V("x", int(v))
                    else:
                        if isinstance(v, bool):
                            md[k] = V("b", bool(v))
                        elif isinstance(v, int):
                            md[k] = V("x", int(v))
                        elif isinstance(v, float):
                            md[k] = V("d", float(v))
                        elif isinstance(v, (list, tuple)):
                            md[k] = V("as", [str(x) for x in v])
                        else:
                            md[k] = V("s", "" if v is None else str(v))
                except Exception:
                    pass
            return md

        # --- Radio metadata ---
        station = getattr(self.app, "current_radio", None)
        stations = getattr(self.app, "radio_stations", {}) or {}
        if station:
            url = stations.get(station, "")
            return pack({
                "mpris:trackid": f"{self.OBJ_PATH}/track/radio",
                "xesam:title": station,
                "xesam:artist": ["Radio"],
                "xesam:url": url,
            })

        # --- Local file metadata ---
        idx = getattr(self.app, "current_song_index", -1)
        playlist = getattr(self.app, "playlist", []) or []
        if not (0 <= idx < len(playlist)):
            return pack({})

        item = playlist[idx] or {}
        path = item.get("path", "")

        # Prefer parsed song data stored by generic_player.py
        title = item.get("title") or None
        artist = item.get("artist") or None
        display = item.get("display") or None

        # If only display is available, try to split it.
        if (not title or not artist) and isinstance(display, str):
            for sep in (" — ", " - ", " – ", " — "):
                if sep in display:
                    a, t = display.split(sep, 1)
                    a, t = a.strip(), t.strip()
                    if not artist and a:
                        artist = a
                    if not title and t:
                        title = t
                    break

        if not title:
            title = os.path.basename(path) or "Unknown"

        # microseconds
        length_us = int(max(0, int(getattr(self.app, "_duration_ms", 0))) * 1000)

        md_plain = {
            "mpris:trackid": f"{self.OBJ_PATH}/track/{idx}",
            "xesam:title": title,
            # Always provide artist as array for best compatibility
            "xesam:artist": [artist] if artist else [],
        }

        if path:
            try:
                md_plain["xesam:url"] = QUrl.fromLocalFile(path).toString()
            except Exception:
                md_plain["xesam:url"] = str(path)

        if length_us > 0:
            md_plain["mpris:length"] = length_us

        return pack(md_plain)

    @property
    def Volume(self):
        # MPRIS volume is 0.0..1.0
        try:
            if hasattr(self.app, "audio_out") and self.app.audio_out is not None:
                return float(self.app.audio_out.volume())
        except Exception:
            pass

        try:
            if hasattr(self.app, "volume_slider") and self.app.volume_slider is not None:
                return float(self.app.volume_slider.value()) / 100.0
        except Exception:
            pass

        try:
            return float(self.app.player.volume()) / 100.0
        except Exception:
            return 0.7

    @Volume.setter
    def Volume(self, value):
        v = float(value)
        if v < 0.0:
            v = 0.0
        if v > 1.0:
            v = 1.0

        # Prefer UI path
        try:
            if hasattr(self.app, "volume_slider") and self.app.volume_slider is not None:
                self.app.volume_slider.setValue(int(v * 100))
                self.notify_volume()
                return
        except Exception:
            pass

        # Qt6 direct
        try:
            if hasattr(self.app, "audio_out") and self.app.audio_out is not None:
                self.app.audio_out.setVolume(v)
                self.notify_volume()
                return
        except Exception:
            pass

        # Qt5 direct
        try:
            self.app.player.setVolume(int(v * 100))
            self.notify_volume()
        except Exception:
            pass

    @property
    def CanGoNext(self):
        try:
            if getattr(self.app, "current_radio", None):
                return False
            return bool(getattr(self.app, "playlist", []))
        except Exception:
            return True

    @property
    def CanGoPrevious(self):
        try:
            if getattr(self.app, "current_radio", None):
                return False
            return bool(getattr(self.app, "playlist", []))
        except Exception:
            return True

    CanPlay = True
    CanPause = True

    @property
    def CanSeek(self):
        try:
            return getattr(self.app, "current_radio", None) is None
        except Exception:
            return True

    CanControl = True

    # ---------------- PropertiesChanged helpers ----------------
    def _emit_properties_changed(self, changed: dict, invalidated=None):
        """Emit org.freedesktop.DBus.Properties.PropertiesChanged."""
        try:
            inv = invalidated or []
            params = GLib.Variant("(sa{sv}as)", (self.PLAYER_IFACE, changed, inv))
            # Gio.DBusConnection.emit_signal(destination, path, iface, signal, params)
            self.bus.con.emit_signal(
                None,
                self.OBJ_PATH,
                "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                params,
            )
        except Exception:
            pass

    def notify_playback(self):
        try:
            self._emit_properties_changed({
                "PlaybackStatus": GLib.Variant("s", self.PlaybackStatus)
            })
        except Exception:
            pass

    def notify_metadata(self):
        try:
            md = self.Metadata
            self._emit_properties_changed({
                "Metadata": GLib.Variant("a{sv}", md)
            })
        except Exception:
            pass

    def notify_volume(self):
        try:
            self._emit_properties_changed({
                "Volume": GLib.Variant("d", float(self.Volume))
            })
        except Exception:
            pass

    # ---------------- Internal helpers ----------------
    def _call_app(self, method_name: str):
        fn = getattr(self.app, method_name, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def _is_playing(self) -> bool:
        # Prefer the app's last confirmed state (Qt signal-driven), if available.
        try:
            return bool(getattr(self.app, "_last_playing", False))
        except Exception:
            pass

        # Fall back to querying the player directly.
        fn = getattr(self.app, "player_is_playing", None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                pass
        try:
            if USING_QT6:
                return self.app.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            return self.app.player.state() == QMediaPlayer.State.PlayingState
        except Exception:
            return False
