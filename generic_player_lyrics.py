#!/usr/bin/env python3
# generic_player_lyrics.py — minimal show/hide lyrics, robust metadata detection
# GPL v2 — JJ Posti (techtimejourney.net) 2025. 

import os, re, sys
sys.dont_write_bytecode = True
from dataclasses import dataclass
from typing import Optional, Tuple

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

# -------- Qt shims --------
USING_QT6 = False
try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    from PyQt6.QtCore import Qt, pyqtSignal
    USING_QT6 = True
except Exception:
    from PyQt5 import QtCore, QtGui, QtWidgets
    from PyQt5.QtCore import Qt, pyqtSignal
    USING_QT6 = False

if USING_QT6:
    DockArea = Qt.DockWidgetArea
else:
    DockArea = Qt

LYRICS_TIMEOUT = 10
LYRICS_USER_AGENT = "Generic_Player-Lyrics/1.2"
DEFAULT_DOCK_WIDTH = 560  # widen the lyrics pane

DARK_STYLESHEET = """
QDockWidget::title { padding: 6px 8px; background: #1a262d; color: #e5e9ec; }
QTextBrowser {
    background: #111e25;
    color: #e5e9ec;
    border: 1px solid #26333a;
    border-radius: 8px;
    padding: 10px;
    font-family: 'Segoe UI', Tahoma, sans-serif;
    font-size: 12px;
}
"""

# -------- Helpers --------
def _strip_ext(name: str) -> str:
    return re.sub(r"\.(mp3|flac|ogg|wav|m4a|aac|wma|mp4|mkv|avi|mov|wmv)$", "", name, flags=re.I)

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

    m = re.match(r"^\s*(.+?)\s*[-–—]\s*(.+?)\s*$", stem)
    if m:
        a, t = _clean_piece(m.group(1)), _clean_piece(m.group(2))
        if a and t:
            return a, t

    m = re.match(r"^\s*\d{1,3}\s*[-–—]\s*(.+?)\s*[-–—]\s*(.+?)\s*$", stem)
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
    if not _HAVE_REQUESTS or not artist or not title:
        return None
    try:
        url = f"https://api.lyrics.ovh/v1/{artist}/{title}"
        res = requests.get(url, timeout=LYRICS_TIMEOUT,
                           headers={"User-Agent": LYRICS_USER_AGENT})
        if res.status_code == 200:
            lyrics = (res.json() or {}).get("lyrics", "") or ""
            if lyrics.strip():
                return LyricsResult(artist=artist, title=title, text=lyrics, source="lyrics.ovh")
    except Exception:
        pass
    return None

# -------- Worker (emits job_id + result) --------
class _LyricsWorker(QtCore.QObject):
    finished = pyqtSignal(int, object)  # job_id, LyricsResult | None

    def __init__(self, job_id: int, path: Optional[str], artist: Optional[str], title: Optional[str]):
        super().__init__()
        self.job_id = job_id
        self.path = path
        self.artist = artist
        self.title = title

    @QtCore.pyqtSlot()
    def run(self):
        a, t = self.artist, self.title

        # 1) tags
        if (not a or not t) and self.path:
            ta, tt = parse_artist_title_from_tags(self.path)
            a = a or ta
            t = t or tt

        # 2) filename patterns
        if (not a or not t) and self.path:
            fa, ft = parse_artist_title_from_filename(self.path)
            a = a or fa
            t = t or ft

        # 3) online fetch
        result = fetch_lyrics(a, t) if (a and t) else None
        self.finished.emit(self.job_id, result)

# -------- Controller --------
class GenericPlayerLyrics(QtCore.QObject):
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
        self.dock.setMinimumWidth(DEFAULT_DOCK_WIDTH)
        self.view.setMinimumWidth(DEFAULT_DOCK_WIDTH - 20)

        try:
            self.win.addDockWidget(DockArea.RightDockWidgetArea, self.dock)
        except Exception:
            pass

        # Style
        try:
            self.win.setStyleSheet((self.win.styleSheet() or "") + DARK_STYLESHEET)
        except Exception:
            pass

        # Thread state
        self._thread: Optional[QtCore.QThread] = None
        self._worker: Optional[_LyricsWorker] = None
        self._job_id = 0  # monotonically increasing

        # Initial UI
        if not _HAVE_REQUESTS:
            self.view.setHtml("<b>Lyrics:</b> <i>Install Python package 'requests' (pip install requests)</i>")
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
        """Called by player when a new local file starts."""
        self._cancel_job()

        # Sidecar first
        if path:
            stem, _ = os.path.splitext(path)
            for cand in (stem + ".lrc", stem + ".txt"):
                if os.path.exists(cand):
                    try:
                        txt = open(cand, "r", encoding="utf-8", errors="replace").read()
                        self._set_text(txt, source=os.path.basename(cand))
                        return
                    except Exception:
                        pass

        if not _HAVE_REQUESTS:
            self.view.setHtml("<b>Lyrics:</b> <i>Install 'requests' to enable online fetching.</i>")
            return

        self.view.setHtml("<i>Fetching lyrics…</i>")

        # New job
        self._job_id += 1
        job_id = self._job_id

        thread = QtCore.QThread(self)
        worker = _LyricsWorker(job_id, path, artist, title)
        worker.moveToThread(thread)

        # Wire up using local 'thread' variable to avoid referencing self._thread later
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_ready, QtCore.Qt.ConnectionType.QueuedConnection if USING_QT6 else QtCore.Qt.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._thread = thread
        self._worker = worker
        thread.start()

    def clear(self):
        self._cancel_job()
        self.view.setHtml("<i>No lyrics.</i>")

    # --- internals ---
    @QtCore.pyqtSlot(int, object)
    def _on_ready(self, job_id: int, res: Optional['LyricsResult']):
        # Ignore late results from older jobs
        if job_id != self._job_id:
            return
        if res and res.text.strip():
            header = ""
            if res.artist or res.title:
                at = f"{res.artist or ''} — {res.title or ''}".strip(" —")
                header = f"<div style='color:#9aa5ae;margin-bottom:6px'>{_html_escape(at)}</div>"
            src = f"<div style='color:#74808a;font-size:11px'>Source: {_html_escape(res.source)}</div>"
            body = f"<pre style='white-space:pre-wrap;margin:0'>{_html_escape(res.text)}</pre>"
            self.view.setHtml(header + body + src)
        else:
            self.view.setHtml("<i>No lyrics found (tried tags, filename and lyrics.ovh).</i>")

    def _set_text(self, text: str, source: Optional[str] = None):
        src = f"<div style='color:#74808a;font-size:11px'>Source: {_html_escape(source)}</div>" if source else ""
        self.view.setHtml(f"<pre style='white-space:pre-wrap;margin:0'>{_html_escape(text)}</pre>{src}")

    def _cancel_job(self):
        """Stop any running worker safely (no dangling signal calls)."""
        t, w = self._thread, self._worker
        self._thread = None
        self._worker = None
        # Bump job id so any late results are ignored
        self._job_id += 1

        if t:
            try:
                # RequestInterruption is best-effort; worker is short-lived anyway
                if hasattr(t, "requestInterruption"):
                    t.requestInterruption()
            except Exception:
                pass
            try:
                t.quit()
            except Exception:
                pass
            try:
                t.wait(1500)  # wait up to 1.5s to finish
            except Exception:
                pass
            try:
                t.deleteLater()
            except Exception:
                pass

    def shutdown(self):
        """Called by main window on exit to avoid QThread abort."""
        try:
            self._cancel_job()
        except Exception:
            pass
        try:
            self.dock.hide()
        except Exception:
            pass


# --- manual test ---
if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    mw = QtWidgets.QMainWindow()
    mw.resize(1200, 800)
    lyr = GenericPlayerLyrics(mw)
    mw.show()
    lyr.show_panel()
    lyr.set_media(None, "Coldplay", "Yellow")
    sys.exit(app.exec() if USING_QT6 else app.exec_())
