#!/usr/bin/env python3
"""On-demand lyrics with bounded input and a worker that never touches Qt.

The GUI prefers local sidecars and submits metadata/network work only when the
dock is visible. Monotonic job IDs invalidate stale work; cancellation never
waits for an HTTP request or lets its result update a different track.
"""
import json
import os
import queue
import re
import sys
import threading
from contextlib import closing
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import quote

sys.dont_write_bytecode = True

# -------- Optional deps --------
try:
    import requests
    _HAVE_REQUESTS = True
except Exception:
    _HAVE_REQUESTS = False

try:
    from mutagen import File as MutagenFile
    _HAVE_MUTAGEN = True
except Exception:
    _HAVE_MUTAGEN = False

# Share the exact binding selected by the main application.
from generic_player_qt import QtCore, QtWidgets, USING_QT6
from generic_player_library import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
Qt = QtCore.Qt

if USING_QT6:
    DockArea = Qt.DockWidgetArea
else:
    DockArea = Qt

LYRICS_TIMEOUT = (3.05, 10)
LYRICS_USER_AGENT = "Generic_Player-Lyrics/1.1"
DEFAULT_DOCK_WIDTH = 340
MAX_LYRICS_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
_MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

# -------- Helpers --------
def _strip_ext(name: str) -> str:
    stem, extension = os.path.splitext(name)
    return stem if extension.lower() in _MEDIA_EXTENSIONS else name

def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&#39;"))

def _ensure_str(val) -> str:
    if val is None:
        return ""
    for attr in ("toPlainText", "toString", "text"):
        fn = getattr(val, attr, None)
        if callable(fn):
            try:
                out = fn()
                return "" if out is None else str(out)
            except Exception:
                pass
    return str(val)

def _clean_piece(s: str) -> str:
    s = re.sub(r"\s*\[[^\]]+\]$", "", s)     # [Live], [Remastered]
    s = re.sub(r"\s*\(.*?\)\s*$", "", s)     # (Radio Edit)
    s = s.replace("_", " ")
    return s.strip(" -_.")

def parse_artist_title_from_tags(path: str) -> Tuple[Optional[str], Optional[str]]:
    if not _HAVE_MUTAGEN:
        return None, None
    try:
        m = MutagenFile(path, easy=True)
        if not m:
            return None, None
        artist = None
        title = None
        for k in ("artist", "ARTIST", "TPE1"):
            v = m.tags.get(k) if m.tags else None
            if v:
                artist = v[0] if isinstance(v, list) else v
                break
        for k in ("title", "TITLE", "TIT2"):
            v = m.tags.get(k) if m.tags else None
            if v:
                title = v[0] if isinstance(v, list) else v
                break
        return (_clean_piece(_ensure_str(artist)) or None,
                _clean_piece(_ensure_str(title)) or None)
    except Exception:
        return None, None

def parse_artist_title_from_filename(path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Common layouts:
      1) /Artist/Album/01 Title.ext  -> (Artist, Title)
      2) Artist - Title.ext
      3) Artist – Title.ext
      4) 01 - Artist - Title.ext
    """
    base = os.path.basename(path)
    stem = _strip_ext(base)

    # Check numbered filenames first: the generic pattern otherwise mistakes
    # the track number for the artist. Keep hyphens inside names like AC-DC.
    m = re.match(r"^\s*\d{1,3}\s*[-–—]\s*(.+?)(?:\s+[-–—]\s+|[–—])(.+?)\s*$", stem)
    if m:
        a, t = _clean_piece(m.group(1)), _clean_piece(m.group(2))
        if a and t:
            return a, t

    m = re.match(r"^\s*(.+?)(?:\s+[-–—]\s+|[–—])(.+?)\s*$", stem)
    if m:
        a, t = _clean_piece(m.group(1)), _clean_piece(m.group(2))
        if a and t:
            return a, t

    parts = os.path.normpath(path).split(os.sep)
    if len(parts) >= 3:
        artist_guess = _clean_piece(parts[-3])
        title_guess = _clean_piece(re.sub(r"^(?:[A-Za-z]?\d{1,3}[\s\.\-_]+)", "", stem))
        if artist_guess and title_guess:
            return artist_guess, title_guess

    return None, _clean_piece(stem) or None

# -------- Data --------
@dataclass
class LyricsResult:
    artist: Optional[str]
    title: Optional[str]
    text: str
    source: str

def fetch_lyrics(artist: str, title: str) -> Optional[LyricsResult]:
    """Return a usable, size-limited response, or None for any provider failure."""
    if not _HAVE_REQUESTS or not artist or not title:
        return None
    try:
        url = f"https://api.lyrics.ovh/v1/{quote(artist, safe='')}/{quote(title, safe='')}"
        with closing(requests.get(url, timeout=LYRICS_TIMEOUT, stream=True,
                                  headers={"User-Agent": LYRICS_USER_AGENT})) as res:
            if res.status_code != 200:
                return None
            # iter_content yields decoded bytes, including for compressed or
            # chunked responses. Bound actual data rather than trusting the
            # optional Content-Length header, and close even on early exits.
            body = bytearray()
            for chunk in res.iter_content(chunk_size=8192):
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    return None
                body.extend(chunk)
            data = json.loads(body)
            lyrics = data.get("lyrics") if isinstance(data, dict) else None
            if (isinstance(lyrics, str) and len(lyrics) <= MAX_LYRICS_BYTES
                    and len(lyrics.encode("utf-8")) <= MAX_LYRICS_BYTES and lyrics.strip()):
                return LyricsResult(artist=artist, title=title, text=lyrics, source="lyrics.ovh")
    except Exception:
        pass
    return None

# -------- Worker with no Qt objects or callbacks --------
class _LyricsService:
    """One bounded worker; rapid changes replace the pending request.

    HTTP requests cannot be forcibly cancelled safely. A daemon thread may
    finish its bounded request after the window closes, but it holds no Qt
    objects and never accesses the GUI. The GUI polls a queue on its own thread.
    This avoids blocking track changes or destroying a running QThread.
    """

    def __init__(self):
        self.results = queue.Queue(maxsize=1)
        self._condition = threading.Condition()
        self._pending = None
        self._latest = -1
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="player-lyrics", daemon=True)
        self._thread.start()

    def submit(self, job_id, path, artist, title):
        """Replace queued work; an already-running request may finish unused."""
        with self._condition:
            if not self._closed:
                self._latest = job_id
                self._pending = (job_id, path, artist, title)
                self._discard_results()
                self._condition.notify()

    def cancel(self, job_id):
        """Advance the accepted ID and release queued work/results immediately."""
        with self._condition:
            self._latest = job_id
            self._pending = None
            self._discard_results()

    def close(self):
        with self._condition:
            self._closed = True
            self._pending = None
            self._discard_results()
            self._condition.notify()

    def _discard_results(self):
        try:
            self.results.get_nowait()
        except queue.Empty:
            pass

    def _run(self):
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                job = self._pending
                self._pending = None
            self._resolve(*job)
            # The idle thread must not keep the last track or lyrics alive.
            # A per-job frame also releases tag/result locals once published.
            job = None

    def _resolve(self, job_id, path, artist, title):
        """Resolve one job outside the condition lock, then publish if current."""
        try:
            if path and (not artist or not title):
                tag_artist, tag_title = parse_artist_title_from_tags(path)
                artist, title = artist or tag_artist, title or tag_title
            if path and (not artist or not title):
                file_artist, file_title = parse_artist_title_from_filename(path)
                artist, title = artist or file_artist, title or file_title
            with self._condition:
                if self._closed or job_id != self._latest:
                    return
            result = fetch_lyrics(artist, title) if artist and title else None
        except Exception:
            result = None
        with self._condition:
            # A track can change during metadata lookup or HTTP I/O. Checking
            # again under the same lock as submit prevents a stale publication.
            if self._closed or job_id != self._latest:
                return
            self._discard_results()
            self.results.put_nowait((job_id, result))

# -------- Controller --------
class GenericPlayerLyrics(QtCore.QObject):
    """Own the dock and poll worker results exclusively on the GUI thread."""
    def __init__(self, main_window: QtWidgets.QMainWindow, video_widget: Optional[QtWidgets.QWidget] = None):
        super().__init__(main_window)
        self.win = main_window

        # Dock
        self.dock = QtWidgets.QDockWidget("Lyrics", self.win)
        self.dock.setObjectName("GenericPlayerLyricsDock")
        self.dock.setAllowedAreas(DockArea.LeftDockWidgetArea | DockArea.RightDockWidgetArea | DockArea.BottomDockWidgetArea)
        self.view = QtWidgets.QTextBrowser(self.dock)
        self.view.setOpenExternalLinks(True)
        self.dock.setWidget(self.view)
        self.dock.setMinimumWidth(280)
        self.view.setMinimumWidth(240)
        self.view.setObjectName("lyrics_view")

        try:
            self.win.addDockWidget(DockArea.RightDockWidgetArea, self.dock)
        except Exception:
            pass

        # Keep Qt objects entirely on the GUI thread. The worker is lazy so
        # opening the player never starts an unnecessary background thread.
        self._service = None
        self._pending_media = None
        self._closed = False
        self._job_id = 0  # Also advances for Clear/Stop, not just new media.
        self._result_timer = QtCore.QTimer(self)
        self._result_timer.setInterval(80)
        self._result_timer.timeout.connect(self._poll_results)
        self.dock.visibilityChanged.connect(self._sync_visibility)

        # Initial UI
        if not _HAVE_REQUESTS:
            self.view.setHtml("<b>Lyrics:</b> <i>Install the python3-requests system package to enable online lyrics.</i>")
        else:
            self.view.setHtml("<i>Toggle Lyrics to view fetched text.</i>")

        self.dock.hide()

    # --- minimal public API used by the player ---
    def show_panel(self):
        self.dock.show()
        try:
            if hasattr(self.win, "resizeDocks"):
                self.win.resizeDocks(
                    [self.dock],
                    [DEFAULT_DOCK_WIDTH],
                    Qt.Orientation.Horizontal if USING_QT6 else Qt.Horizontal
                )
        except Exception:
            pass
        try:
            if self.dock.isFloating():
                h = max(self.dock.height(), 420)
                self.dock.resize(DEFAULT_DOCK_WIDTH, h)
        except Exception:
            pass

    def hide_panel(self):
        self.dock.hide()

    def isVisible(self):
        return self.dock.isVisible()

    def toggle(self):
        if self.dock.isVisible():
            self.hide_panel()
        else:
            self.show_panel()

    def set_media(self, path: Optional[str], artist: Optional[str] = None, title: Optional[str] = None):
        """Remember the latest track; load lyrics only when they can be viewed."""
        if self._closed:
            return
        self._cancel_job()
        self._pending_media = (path, artist, title)
        if self.dock.isVisible():
            self._load_pending_media()
        else:
            self.view.setHtml("<i>Open Lyrics to load text for this track.</i>")

    def _load_pending_media(self):
        """Consume the latest hidden selection once; reopening reuses its text."""
        if self._closed or self._pending_media is None:
            return
        path, artist, title = self._pending_media
        # Clear before loading: hide/show during a request must not resubmit it.
        self._pending_media = None

        # Sidecar first
        if path:
            stem, _ = os.path.splitext(path)
            for cand in (stem + ".lrc", stem + ".txt"):
                if os.path.isfile(cand):
                    try:
                        with open(cand, "rb") as sidecar:
                            data = sidecar.read(MAX_LYRICS_BYTES + 1)
                        if len(data) > MAX_LYRICS_BYTES:
                            self.view.setHtml("<i>Lyrics file is too large. "
                                              "Use a .lrc or .txt file of 256 KiB or less.</i>")
                            return
                        txt = data.decode("utf-8-sig", errors="replace")
                        self._set_text(txt, source=os.path.basename(cand))
                        return
                    except Exception:
                        pass

        if not _HAVE_REQUESTS:
            self.view.setHtml("<b>Lyrics:</b> <i>Install 'requests' to enable online fetching.</i>")
            return

        self.view.setHtml("<i>Fetching lyrics…</i>")

        if self._service is None:
            self._service = _LyricsService()
            self.destroyed.connect(lambda _obj=None, service=self._service: service.close())
        self._service.submit(self._job_id, path, artist, title)
        self._result_timer.start()

    def clear(self):
        self._pending_media = None
        self._cancel_job()
        self.view.setHtml("<i>No lyrics.</i>")

    # --- internals ---
    @QtCore.pyqtSlot(int, object)
    def _on_ready(self, job_id: int, res: Optional['LyricsResult']):
        # Ignore late results from older jobs
        if self._closed or job_id != self._job_id:
            return
        if res and res.text.strip():
            header = ""
            if res.artist or res.title:
                at = f"{res.artist or ''} — {res.title or ''}".strip(" —")
                header = f"<div style='font-weight:600;margin-bottom:6px'>{_html_escape(at)}</div>"
            src = f"<div style='font-size:11px'>Source: {_html_escape(res.source)}</div>"
            body = f"<pre style='white-space:pre-wrap;margin:0'>{_html_escape(res.text)}</pre>"
            self.view.setHtml(header + body + src)
        else:
            self.view.setHtml("<i>No lyrics found, or the provider response could not be used. "
                              "You can add a .lrc or .txt sidecar of up to 256 KiB.</i>")

    def _set_text(self, text: str, source: Optional[str] = None):
        src = f"<div style='font-size:11px'>Source: {_html_escape(source)}</div>" if source else ""
        self.view.setHtml(f"<pre style='white-space:pre-wrap;margin:0'>{_html_escape(text)}</pre>{src}")

    def _cancel_job(self):
        """Invalidate the request immediately without waiting for network I/O."""
        self._job_id += 1
        self._result_timer.stop()
        if self._service is not None:
            self._service.cancel(self._job_id)

    def _poll_results(self):
        """Transfer plain worker results to Qt; stop polling once this job ends."""
        if self._closed or self._service is None:
            return
        while True:
            try:
                job_id, result = self._service.results.get_nowait()
            except queue.Empty:
                return
            if job_id == self._job_id:
                self._on_ready(job_id, result)
                self._result_timer.stop()

    def _sync_visibility(self, visible):
        # Closing the dock with its title-bar X must update the main toolbar.
        self.win._lyrics_visible = bool(visible)
        button = getattr(self.win, "lyrics_button", None)
        if button is not None:
            button.setChecked(bool(visible))
            button.setText("Hide Lyrics" if visible else "Lyrics")
        if visible:
            self._load_pending_media()

    def shutdown(self):
        """Detach immediately; an in-flight HTTP request cannot touch Qt."""
        self._closed = True
        self._pending_media = None
        self._cancel_job()
        if self._service is not None:
            self._service.close()
        self.dock.hide()
