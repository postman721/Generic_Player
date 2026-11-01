"""Optional, bounded desktop cards driven by the player's own state changes.

Automatic mode discovers real sockets and notification services, then verifies
installed Wayland clients. Explicit adapters also support arbitrary OSD tools.
Qt handles sockets, child processes and D-Bus asynchronously, retaining only
the newest snapshot. No OSD package is required for normal playback. Desktop
notifications request five seconds; the desktop controls their presentation.
"""
from __future__ import annotations

import html
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import string
import sys

from generic_player_metadata import normalize_year, safe_metadata_text
from generic_player_qt import QtCore, USING_QT6


def _optional_qt_module(name):
    try:
        return importlib.import_module(f"PyQt{'6' if USING_QT6 else '5'}.{name}")
    except (ImportError, RuntimeError):
        return None


QtDBus = _optional_qt_module("QtDBus")
QtNetwork = _optional_qt_module("QtNetwork")
NOTIFICATIONS_SERVICE = "org.freedesktop.Notifications"
NOTIFICATIONS_PATH = "/org/freedesktop/Notifications"
DISPLAY_MS = 5000
_COMMAND_PATHS = ("/usr/bin/osd", "/usr/local/bin/osd")
_LEGACY_COMMAND_PATHS = ("/usr/bin/wayland-volume-osd", "/usr/local/bin/wayland-volume-osd")
_COMMAND_ALIASES = ("wayland-osd", "wayland_osd")
_HELP_LIMIT = 32768
_ARGV_LIMIT = 32768
_ARGV_FIELDS = frozenset({"title", "artist", "album", "year", "status", "text", "timeout_ms"})


def _absolute_path(value):
    """Environment overrides name one path, never a shell command."""
    if not isinstance(value, str) or not value or "\0" in value:
        return None
    try:
        path = Path(value).expanduser()
        return str(path) if path.is_absolute() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _command_paths():
    if "GENERIC_PLAYER_OSD_COMMAND" in os.environ:
        candidates = [_absolute_path(os.environ["GENERIC_PLAYER_OSD_COMMAND"])]
    else:
        candidates = [*_COMMAND_PATHS, shutil.which("osd")]
        for name in _COMMAND_ALIASES:
            candidates.extend((f"/usr/bin/{name}", f"/usr/local/bin/{name}", shutil.which(name)))
        candidates.extend((*_LEGACY_COMMAND_PATHS, shutil.which("wayland-volume-osd")))
    result = []
    for candidate in candidates:
        if not candidate or candidate in result:
            continue
        try:
            candidate = os.path.abspath(candidate)
            if stat.S_ISREG(os.stat(candidate).st_mode) and os.access(candidate, os.X_OK):
                if candidate not in result:
                    result.append(candidate)
        except (OSError, ValueError):
            continue
    return result


def find_osd_commands():
    """Return installed candidates; compatibility is verified before use.

    This filesystem-only inspection is safe for dependency checks. A filename
    alone never establishes that a program implements the Wayland protocol.
    """
    return _command_paths()


def find_osd_command():
    """Find an installed executable without running it or opening a session."""
    return next(iter(_command_paths()), None)


def custom_osd_argv(snapshot=None):
    """Validate/expand an explicit JSON argv adapter without executing it.

    Missing configuration returns []; an invalid explicit adapter raises
    ValueError. No shell, format expressions, conversions or format specs are
    accepted. Braces can be escaped as {{ and }}. Limits apply both before
    parsing and while expanding, so untrusted song metadata stays bounded.
    """
    if "GENERIC_PLAYER_OSD_ARGV" not in os.environ:
        return []
    raw = os.environ["GENERIC_PLAYER_OSD_ARGV"]
    if len(raw) > _ARGV_LIMIT:
        raise ValueError("GENERIC_PLAYER_OSD_ARGV exceeds 32768 characters")
    try:
        templates = json.loads(raw)
    except (ValueError, RecursionError):
        raise ValueError("GENERIC_PLAYER_OSD_ARGV must be a JSON array of strings") from None
    if (not isinstance(templates, list) or not 1 <= len(templates) <= 32
            or any(not isinstance(value, str) or len(value) > 4096 or "\0" in value
                   for value in templates)):
        raise ValueError("GENERIC_PLAYER_OSD_ARGV requires 1–32 strings, each at most 4096 characters without NUL")
    try:
        for value in templates:
            value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("GENERIC_PLAYER_OSD_ARGV must contain valid Unicode strings") from None
    executable = templates[0]
    # The program is literal: track metadata can never choose which file runs.
    if not executable or "{" in executable or "}" in executable:
        raise ValueError("The first custom OSD argument must be a literal executable")
    if os.path.isabs(executable):
        program = executable
    elif os.path.basename(executable) == executable:
        program = shutil.which(executable)
    else:
        program = None
    try:
        valid = program and stat.S_ISREG(os.stat(program).st_mode) and os.access(program, os.X_OK)
    except (OSError, ValueError):
        valid = False
    if not valid:
        raise ValueError("The custom OSD executable must exist as an absolute path or a command on PATH")
    snapshot = snapshot if snapshot is not None else _snapshot({}, "Stopped")
    _identity, artist, title, year, status = snapshot[:5]
    values = {"title": title, "artist": artist, "album": snapshot[5] if len(snapshot) > 5 else "",
              "year": year or "", "status": status, "text": _card(snapshot)[1],
              "timeout_ms": str(DISPLAY_MS)}
    result = [os.path.abspath(program)]
    total = len(result[0])
    formatter = string.Formatter()
    for template in templates[1:]:
        parts, length = [], 0
        try:
            parsed = formatter.parse(template)
            for literal, field, spec, conversion in parsed:
                if field is not None and (field not in _ARGV_FIELDS or spec or conversion):
                    raise ValueError("Unsupported custom OSD placeholder or format expression")
                addition = literal + (str(values[field]) if field is not None else "")
                length += len(addition)
                if length > 8192 or total + length > 65536:
                    raise ValueError("Expanded custom OSD arguments exceed the size limit")
                parts.append(addition)
        except ValueError as error:
            raise ValueError(f"Invalid GENERIC_PLAYER_OSD_ARGV template: {error}") from None
        result.append("".join(parts))
        total += length
    return result


def _command_signature(path):
    try:
        info = os.stat(path)
        return path, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    except OSError:
        return None


def _compatible_help(data):
    """Recognize the documented client before invoking any media action.

    An executable called osd can be unrelated software. In particular, the
    Wayland_OSD 'show' command displays volume, not arbitrary message text.
    """
    text = data.decode("utf-8", "replace")
    return ("wayland" in text.lower() and "osd" in text.lower()
            and all(flag in text for flag in ("--timeout", "--no-daemon", "--config"))
            and all(re.search(r"\b" + command + r"\b", text)
                    for command in ("media", "daemon", "setup")))


def _variant(value, kind):
    """Specify wire types that Python's int/list inference cannot preserve."""
    result = QtCore.QVariant(value)
    target = (QtCore.QMetaType(getattr(QtCore.QMetaType.Type, kind).value)
              if USING_QT6 else getattr(QtCore.QVariant, kind))
    if not result.convert(target):
        raise ValueError(f"Cannot encode notification {kind} value")
    return result


def _snapshot(media, status):
    media = media if isinstance(media, dict) else {}
    status = status if status in ("Playing", "Paused", "Stopped") else "Stopped"
    # Dates are presentation metadata, never inferred from a filename or mtime.
    year = normalize_year(media.get("year")) or normalize_year(media.get("date"))
    title = safe_metadata_text(media.get("title") or media.get("display"), 240)
    if not title:
        title = "Unknown track" if media else "No track selected"
    artist = safe_metadata_text(media.get("artist"), 240)
    identity = (safe_metadata_text(media.get("id"), 256),
                safe_metadata_text(media.get("path"), 4096))
    return identity, artist, title, year, status, safe_metadata_text(media.get("album"), 240)


def _card(snapshot):
    _identity, artist, song, year, status = snapshot[:5]
    detail = f"{artist} — {song}" if artist else song
    if year:
        detail += f" ({year})"
    return status, detail


def _socket_paths():
    getuid = getattr(os, "getuid", None)
    if not callable(getuid):
        return []
    uid = getuid()
    if "GENERIC_PLAYER_OSD_SOCKET" in os.environ:
        paths = [_absolute_path(os.environ["GENERIC_PLAYER_OSD_SOCKET"])]
    else:
        runtime = _absolute_path(os.environ.get("XDG_RUNTIME_DIR"))
        paths = ([str(Path(runtime) / "wayland-volume-osd.sock")] if runtime else [])
        paths.extend((f"/run/user/{uid}/wayland-volume-osd.sock",
                      f"/tmp/wayland-volume-osd-{uid}/wayland-volume-osd.sock"))
    result = []
    for value in paths:
        if not value or value in result:
            continue
        try:
            info = Path(value).lstat()
            if stat.S_ISSOCK(info.st_mode) and info.st_uid == uid:
                result.append(value)
        except (OSError, ValueError):
            continue
    return result


def _socket_path():
    """Compatibility helper; delivery attempts every valid candidate."""
    return next(iter(_socket_paths()), None)


class _NotificationSignals(QtCore.QObject):
    """Bind each D-Bus signal connection to one immutable daemon owner."""

    def __init__(self, adapter, owner):
        super().__init__(adapter)
        self._adapter = adapter
        self._owner = owner

    @QtCore.pyqtSlot("uint", "uint")
    def closed(self, identity, _reason):
        self._adapter._notification_closed(self._owner, identity)


class DesktopOSD(QtCore.QObject):
    """Best-effort cards; absence or failure never interrupts media playback."""

    unavailable = QtCore.pyqtSignal(str)
    selected = QtCore.pyqtSignal(str)
    MODES = frozenset({"auto", "off", "desktop", "wayland", "custom"})

    def __init__(self, parent=None, mode="auto"):
        super().__init__(parent)
        self.mode = "off"
        self._closed = False
        self._generation = 0
        self._latest = None
        self._last_attempt = None
        self._last_error = None
        self._last_selected = ""
        self._route = ""
        self._auto_stage = 0
        self._socket = None
        self._socket_payload = None
        self._socket_address = ""
        self._socket_candidates = []
        self._failed_socket_paths = set()
        self._wayland_snapshot = None
        self._may_bootstrap = False
        self._command_queue = []
        self._validated_command = None
        self._unsupported_commands = set()
        self._last_client_program = ""
        self._process = None
        self._process_phase = ""
        self._process_program = ""
        self._process_signature = None
        self._process_text = bytearray()
        self._process_failed = False
        self._pending_call = None
        self._pending_kind = ""
        self._service_watcher = None
        self._bus = None
        self._notification_id = 0
        self._notification_owner = ""
        self._notification_info_owner = ""
        self._notification_server = ""
        self._notification_capabilities = None
        self._notification_listener = None
        self._notification_listener_bus = None
        self._closed_notification_ids = set()
        self._coalesce = QtCore.QTimer(self)
        self._coalesce.setSingleShot(True)
        self._coalesce.setInterval(120)
        self._coalesce.timeout.connect(self._flush)
        self._socket_deadline = QtCore.QTimer(self)
        self._socket_deadline.setSingleShot(True)
        self._socket_deadline.setInterval(500)
        self._socket_deadline.timeout.connect(self._socket_timeout)
        self._process_deadline = QtCore.QTimer(self)
        self._process_deadline.setSingleShot(True)
        self._process_deadline.timeout.connect(self._process_timeout)
        self.set_mode(mode)

    def set_mode(self, mode):
        """Change preference without replaying a restored or stale track card."""
        mode = mode if isinstance(mode, str) and mode in self.MODES else "off"
        if self._closed:
            return
        if mode == self.mode:
            self.retry()
            return
        self._generation += 1
        self._coalesce.stop()
        self._close_notification()
        self._cancel_transports()
        self.mode = mode
        self._latest = self._last_attempt = self._last_error = None
        self._last_selected = ""
        self._unsupported_commands.clear()
        self._validated_command = None
        if mode in {"auto", "desktop"} and QtDBus is not None:
            try:
                self._bus = QtDBus.QDBusConnection.sessionBus()
                flags = (QtDBus.QDBusServiceWatcher.WatchModeFlag.WatchForOwnerChange
                         if USING_QT6 else QtDBus.QDBusServiceWatcher.WatchForOwnerChange)
                self._service_watcher = QtDBus.QDBusServiceWatcher(
                    NOTIFICATIONS_SERVICE, self._bus, flags, self)
                self._service_watcher.serviceOwnerChanged.connect(self._owner_changed)
            except (RuntimeError, TypeError):
                self._cancel_transports()

    def retry(self):
        """Retry an explicit user request, without polling an absent service."""
        if self._closed or self.mode == "off" or self._latest is None:
            return
        self._last_attempt = None
        self._last_error = None
        self._last_selected = ""
        self._unsupported_commands.clear()
        self._validated_command = None
        self._notification_info_owner = ""
        self._notification_server = ""
        self._notification_capabilities = None
        self._coalesce.start(120)

    def update(self, media, status):
        """Coalesce load/state bursts, ignoring position, duration and volume."""
        if self._closed or self.mode == "off":
            return
        if not isinstance(media, dict) or not media:
            # Empty queues never create a "No track selected" banner. Invalidate
            # a pending song immediately, while retaining service discovery.
            self._generation += 1
            self._coalesce.stop()
            self._close_notification()
            self._cancel_transports(keep_discovery=True)
            self._latest = self._last_attempt = None
            return
        snapshot = _snapshot(media, status)
        if snapshot == self._latest:
            return
        self._latest = snapshot
        self._coalesce.start(120)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._coalesce.stop()
        self._close_notification()
        self._cancel_transports()
        self._latest = self._last_attempt = None
        self.mode = "off"

    def _report(self, message):
        self._last_selected = ""
        if message != self._last_error and not self._closed:
            self._last_error = message
            self.unavailable.emit(message)

    def _announce(self, backend):
        self._last_error = None
        if backend != self._last_selected and not self._closed:
            self._last_selected = backend
            self.selected.emit(backend)

    def _flush(self):
        if (self._closed or self.mode == "off" or self._latest is None
                or self._latest == self._last_attempt):
            return
        if self._socket is not None or self._pending_call is not None or self._process is not None:
            return  # Completion dispatches the newest item; no queue accumulates.
        self._last_attempt = self._latest
        self._auto_stage = 0
        self._failed_socket_paths.clear()
        try:
            if self.mode == "custom" or (self.mode == "auto" and "GENERIC_PLAYER_OSD_ARGV" in os.environ):
                self._send_custom(self._latest)
            elif self.mode == "wayland" or (self.mode == "auto" and
                    any(name in os.environ for name in ("GENERIC_PLAYER_OSD_SOCKET", "GENERIC_PLAYER_OSD_COMMAND"))):
                self._send_wayland(self._latest)
            elif self.mode == "auto":
                # The sequence is finite: existing socket -> active desktop
                # service -> verified CLI. A failed stage is never re-entered.
                self._auto_stage = 1
                self._send_wayland(self._latest, bootstrap=False)
            else:
                self._send_notification(self._latest)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            # Optional transports must never propagate exceptions out of a Qt
            # callback, where some bindings would terminate the application.
            self._report(f"The selected OSD is unavailable: {safe_metadata_text(str(error), 240)}. Local playback is unaffected.")

    def _send_custom(self, snapshot):
        self._route = "custom"
        try:
            arguments = custom_osd_argv(snapshot)
        except ValueError as error:
            self._report(str(error))
            return
        if not arguments:
            self._report("Custom OSD requires GENERIC_PLAYER_OSD_ARGV: a JSON array containing an executable and its arguments.")
            return
        self._start_process(arguments[0], "custom", arguments[1:])

    def _send_wayland(self, snapshot, bootstrap=True):
        self._route = "wayland"
        if "GENERIC_PLAYER_OSD_SOCKET" in os.environ and not _socket_paths():
            self._wayland_unavailable("GENERIC_PLAYER_OSD_SOCKET must name an existing, owned Unix socket using the Wayland_OSD JSON protocol.")
            return
        if ("GENERIC_PLAYER_OSD_SOCKET" not in os.environ
                and "GENERIC_PLAYER_OSD_COMMAND" in os.environ and not _command_paths()):
            self._wayland_unavailable("GENERIC_PLAYER_OSD_COMMAND must name one existing absolute executable path, without command-line arguments.")
            return
        if QtNetwork is None:
            self._wayland_unavailable("QtNetwork is unavailable; the optional OSD connection cannot send song details.")
            return
        self._wayland_snapshot = snapshot
        self._socket_candidates = [path for path in _socket_paths() if path not in self._failed_socket_paths]
        # The upstream client cannot create a Generic-specific custom socket.
        # A configured endpoint must already exist; do not start another daemon
        # at a different default location when that endpoint is unavailable.
        self._may_bootstrap = bootstrap and "GENERIC_PLAYER_OSD_SOCKET" not in os.environ
        self._last_client_program = ""
        self._next_wayland_socket()

    def _next_wayland_socket(self):
        if self._closed or self._route != "wayland" or self._latest is None:
            return
        if not self._socket_candidates:
            if self._may_bootstrap:
                self._may_bootstrap = False
                self._command_queue = _command_paths()
                self._next_command()
            elif self._command_queue:
                self._next_command()
            else:
                command = safe_metadata_text(self._last_client_program, 180)
                self._wayland_unavailable(f"OSD client {command or 'connection'} could not reach its compatible socket. "
                                          "Check the OSD session or set GENERIC_PLAYER_OSD_SOCKET, then retry the song notification.")
            return
        path = self._socket_candidates.pop(0)
        self._socket_address = path
        # A previous connection attempt may have outlived a rapid track change.
        snapshot = self._wayland_snapshot = self._latest
        self._last_attempt = snapshot
        title, detail = _card(snapshot)
        payload = {"type": "show", "mode": "media", "title": title,
                   "detail": detail, "level": 100 if title == "Playing" else 0}
        self._socket_payload = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        sock = self._socket = QtNetwork.QLocalSocket(self)
        generation = self._generation
        sock.connected.connect(lambda: self._socket_connected(sock, generation))
        sock.bytesWritten.connect(lambda _count: self._socket_written(sock, generation))
        error_signal = getattr(sock, "errorOccurred", None)
        if error_signal is None:
            error_signal = sock.error
        error_signal.connect(lambda _error: self._socket_failed(sock, generation))
        self._socket_deadline.start()
        sock.connectToServer(path)

    def _socket_connected(self, sock, generation):
        if sock is not self._socket or generation != self._generation:
            return
        payload = self._socket_payload
        if payload is None or sock.write(payload) != len(payload):
            self._socket_failed(sock, generation)

    def _socket_written(self, sock, generation):
        if sock is self._socket and generation == self._generation and sock.bytesToWrite() == 0:
            self._last_error = None
            self._finish_socket(True)

    def _socket_failed(self, sock, generation):
        if sock is self._socket and generation == self._generation:
            self._finish_socket(False)

    def _socket_timeout(self):
        if self._socket is not None:
            self._socket_failed(self._socket, self._generation)

    def _finish_socket(self, delivered):
        self._socket_deadline.stop()
        sock, self._socket = self._socket, None
        self._socket_payload = None
        path, self._socket_address = self._socket_address, ""
        if sock is not None:
            sock.abort()
            sock.deleteLater()
        if delivered:
            self._announce(f"Wayland OSD — {path}")
            self._complete_wayland()
        else:
            self._failed_socket_paths.add(path)
            self._next_wayland_socket()

    def _wayland_unavailable(self, message):
        if self._auto_stage == 1:
            self._wayland_snapshot = None
            self._socket_candidates.clear()
            self._command_queue.clear()
            self._auto_stage = 2
            self._send_notification(self._latest)
            return
        self._report(message)
        self._complete_wayland()

    def _complete_wayland(self):
        self._wayland_snapshot = None
        self._socket_candidates.clear()
        self._command_queue.clear()
        self._dispatch_latest()

    def _next_command(self):
        """Validate only installed candidates, one bounded process at a time."""
        while self._command_queue:
            program = self._command_queue.pop(0)
            self._last_client_program = program
            signature = _command_signature(program)
            if signature is None or signature in self._unsupported_commands:
                continue
            self._start_process(program, "media" if signature == self._validated_command else "help")
            return
        command = safe_metadata_text(self._last_client_program, 180)
        if command:
            message = (f"Found {command}, but a compatible Wayland_OSD service could not be reached. "
                       "Use GENERIC_PLAYER_OSD_ARGV for a different OSD interface, or set the correct command/socket override.")
        elif "GENERIC_PLAYER_OSD_COMMAND" in os.environ:
            configured = safe_metadata_text(os.environ["GENERIC_PLAYER_OSD_COMMAND"], 180)
            message = (f"Configured OSD executable {configured} is unavailable. "
                       "GENERIC_PLAYER_OSD_COMMAND must name one absolute executable path, without command-line arguments.")
        else:
            message = ("No compatible OSD was found. Checked the notification service and osd, wayland-osd, "
                       "wayland_osd, wayland-volume-osd in /usr/bin, /usr/local/bin and PATH. "
                       "Use GENERIC_PLAYER_OSD_ARGV to configure another OSD. Local playback is unaffected.")
        self._wayland_unavailable(message)

    def _start_process(self, program, phase, arguments=None):
        process = self._process = QtCore.QProcess(self)
        self._process_phase, self._process_program = phase, program
        self._process_signature = _command_signature(program)
        self._process_text.clear()
        self._process_failed = False
        generation = self._generation
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        runtime = environment.value("XDG_RUNTIME_DIR")
        normalized_runtime = _absolute_path(runtime)
        if runtime and normalized_runtime is None:
            # Upstream accepts a relative runtime directory literally. Do not
            # let it create a socket in the working directory that our absolute
            # path discovery will never see; use its supported /tmp fallback.
            environment.remove("XDG_RUNTIME_DIR")
        elif normalized_runtime is not None:
            environment.insert("XDG_RUNTIME_DIR", normalized_runtime)
        process.setProcessEnvironment(environment)
        process.setStandardInputFile(QtCore.QProcess.nullDevice())
        if phase == "help":
            channels = QtCore.QProcess.ProcessChannelMode.MergedChannels if USING_QT6 else QtCore.QProcess.MergedChannels
            process.setProcessChannelMode(channels)
            process.readyReadStandardOutput.connect(lambda: self._read_process_help(process))
        else:
            # The normal client can print metadata or diagnostics indefinitely.
            # Output is not needed for the protocol and never accumulates in Qt.
            process.setStandardOutputFile(QtCore.QProcess.nullDevice())
            process.setStandardErrorFile(QtCore.QProcess.nullDevice())
        process.finished.connect(lambda code, status: self._process_finished(process, generation, code, status))
        process.errorOccurred.connect(lambda error: self._process_error(process, generation, error))
        self._process_deadline.start(1000 if phase == "help" else 3000)
        if arguments is None:
            arguments = ["--help"] if phase == "help" else ["--timeout", str(DISPLAY_MS), "media"]
        if hasattr(os, "setsid"):
            # A private process group also contains temporary playerctl helpers.
            # The actual OSD daemon starts its own session and is not ours to
            # kill. This fixed argv-only exec shim works with both Qt bindings.
            launcher = "import os,sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])"
            process.start(sys.executable, ["-I", "-S", "-c", launcher, program, *arguments])
        else:
            process.start(program, arguments)

    def _read_process_help(self, process):
        if process is not self._process or self._process_phase != "help":
            return
        remaining = max(0, _HELP_LIMIT - len(self._process_text))
        # Read at most one byte beyond the cap. readAll would first duplicate
        # an arbitrarily large native buffer before we could discard its tail.
        # PyQt can return None at EOF after a killed or failed process. An
        # exception escaping the finished callback would abort the GUI.
        data = bytes(process.read(remaining + 1) or b"")
        self._process_text.extend(data[:remaining])
        if len(data) > remaining:
            self._process_failed = True
            self._kill_process(process)

    def _process_error(self, process, generation, error):
        failed = QtCore.QProcess.ProcessError.FailedToStart if USING_QT6 else QtCore.QProcess.FailedToStart
        if process is self._process and error == failed:
            crash = QtCore.QProcess.ExitStatus.CrashExit if USING_QT6 else QtCore.QProcess.CrashExit
            self._process_finished(process, generation, -1, crash)

    def _process_timeout(self):
        if self._process is not None:
            self._process_failed = True
            self._kill_process(self._process)

    @staticmethod
    def _kill_process(process):
        """Stop only this client's private group; reap through Qt's finished signal."""
        pid = int(process.processId())
        if pid and hasattr(os, "getpgid"):
            try:
                if os.getpgid(pid) == pid:
                    os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        process.kill()

    def _process_finished(self, process, generation, code, status):
        if process is not self._process:
            return
        self._read_process_help(process)
        phase, program = self._process_phase, self._process_program
        initial_signature = self._process_signature
        output, failed = bytes(self._process_text), self._process_failed
        self._process = None
        self._process_deadline.stop()
        self._process_phase = self._process_program = ""
        self._process_signature = None
        self._process_text.clear()
        process.deleteLater()
        if generation != self._generation or self._closed or self._latest is None:
            self._dispatch_latest()
            return
        normal = QtCore.QProcess.ExitStatus.NormalExit if USING_QT6 else QtCore.QProcess.NormalExit
        if phase == "custom":
            if not failed and code == 0 and status == normal:
                self._announce(f"Custom command — {program}")
            else:
                self._report("The custom OSD command failed or exceeded its three-second deadline. Local playback is unaffected.")
            self._dispatch_latest()
        elif phase == "help":
            signature = _command_signature(program)
            if (not failed and code == 0 and status == normal
                    and signature is not None and signature == initial_signature and _compatible_help(output)):
                self._validated_command = signature
                self._start_process(program, "media")
            else:
                if len(self._unsupported_commands) >= 8:
                    self._unsupported_commands.clear()
                self._unsupported_commands.add(signature)
                self._next_command()
        else:
            # 'media' safely starts/reuses the upstream daemon. It can show its
            # own short card first; replace that with our complete metadata.
            # Never call 'daemon' directly: that command unlinks its socket.
            self._socket_candidates = _socket_paths()
            self._next_wayland_socket()

    def _send_notification(self, snapshot):
        self._route = "desktop"
        if QtDBus is None or self._bus is None or not self._bus.isConnected():
            self._notification_unavailable("Desktop notifications are unavailable in this session.")
            return
        if not self._notification_owner:
            self._find_notification_owner()
            return
        if self._notification_info_owner != self._notification_owner:
            self._find_notification_details("GetServerInformation")
            return
        if self._notification_capabilities is None:
            self._find_notification_details("GetCapabilities")
            return
        title, detail = _card(snapshot)
        if "body" in self._notification_capabilities:
            body = html.escape(detail, quote=False) if "body-markup" in self._notification_capabilities else detail
        else:
            # Body support is optional. Keep all song details visible even on
            # a minimal custom notification daemon that only shows summaries.
            title, body = f"{title} — {detail}", ""
        # Address a verified unique owner directly. Its notification IDs
        # must never be sent to a replacement daemon that may reuse numbers.
        message = QtDBus.QDBusMessage.createMethodCall(
            self._notification_owner, NOTIFICATIONS_PATH, NOTIFICATIONS_SERVICE, "Notify")
        message.setAutoStartService(False)
        message.setArguments([
            "Generic Player", _variant(self._notification_id, "UInt"), "generic-player",
            title, body, _variant([], "StringList" if not USING_QT6 else "QStringList"),
            {"desktop-entry": "generic-player", "transient": True, "suppress-sound": True},
            _variant(DISPLAY_MS, "Int"),
        ])
        generation = self._generation
        bus, owner = self._bus, self._notification_owner
        pending = bus.asyncCall(message, 750)
        watcher = self._pending_call = QtDBus.QDBusPendingCallWatcher(pending, self)
        self._pending_kind = "notify"
        watcher.finished.connect(lambda call: self._notification_finished(call, generation, bus, owner))

    def _find_notification_owner(self):
        # Resolving asynchronously also avoids activating a missing service.
        message = QtDBus.QDBusMessage.createMethodCall(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetNameOwner")
        message.setArguments([NOTIFICATIONS_SERVICE])
        generation = self._generation
        pending = self._bus.asyncCall(message, 750)
        watcher = self._pending_call = QtDBus.QDBusPendingCallWatcher(pending, self)
        self._pending_kind = "owner"
        watcher.finished.connect(lambda call: self._owner_resolved(call, generation))

    def _owner_resolved(self, watcher, generation):
        if watcher is not self._pending_call or generation != self._generation:
            return
        self._pending_call = None
        self._pending_kind = ""
        reply = QtDBus.QDBusPendingReply(watcher)
        args = reply.reply().arguments()
        watcher.deleteLater()
        owner = args[0] if args else None
        if reply.isError() or not isinstance(owner, str) or not owner.startswith(":"):
            self._notification_unavailable("Desktop notifications are unavailable in this session.")
            return
        self._notification_owner = owner
        self._connect_notification_signals(owner)
        self._last_attempt = self._latest
        try:
            self._send_notification(self._latest)
        except (RuntimeError, TypeError, ValueError):
            self._notification_unavailable("Desktop notifications are unavailable in this session.")

    def _find_notification_details(self, method):
        owner, generation = self._notification_owner, self._generation
        message = QtDBus.QDBusMessage.createMethodCall(
            owner, NOTIFICATIONS_PATH, NOTIFICATIONS_SERVICE, method)
        message.setAutoStartService(False)
        pending = self._bus.asyncCall(message, 750)
        watcher = self._pending_call = QtDBus.QDBusPendingCallWatcher(pending, self)
        self._pending_kind = "details"
        watcher.finished.connect(lambda call: self._notification_details(call, generation, owner, method))

    def _notification_details(self, watcher, generation, owner, method):
        if watcher is not self._pending_call or generation != self._generation:
            return
        self._pending_call = None
        self._pending_kind = ""
        pending = QtDBus.QDBusPendingReply(watcher)
        args = pending.reply().arguments()
        watcher.deleteLater()
        if owner != self._notification_owner:
            self._dispatch_latest()
            return
        if method == "GetServerInformation":
            self._notification_info_owner = owner
            self._notification_server = (safe_metadata_text(args[0], 80)
                                         if not pending.isError() and len(args) == 4
                                         and all(isinstance(value, str) for value in args) else "")
        else:
            values = args[0] if not pending.isError() and len(args) == 1 else []
            # Only two standard capability flags affect rendering. Never retain
            # arbitrary provider data, and degrade safely if probing fails.
            self._notification_capabilities = frozenset(
                value for value in (values[:128] if isinstance(values, (list, tuple)) else [])
                if isinstance(value, str) and value in {"body", "body-markup"})
        self._last_attempt = self._latest
        try:
            self._send_notification(self._latest)
        except (RuntimeError, TypeError, ValueError):
            self._notification_unavailable("Desktop notifications are unavailable in this session.")

    def _notification_finished(self, watcher, generation, bus=None, owner=None):
        if watcher is not self._pending_call:
            return
        self._pending_call = None
        self._pending_kind = ""
        reply = QtDBus.QDBusPendingReply(watcher).reply()
        watcher.deleteLater()
        error_type = (QtDBus.QDBusMessage.MessageType.ErrorMessage if USING_QT6
                      else QtDBus.QDBusMessage.ErrorMessage)
        args = reply.arguments()
        identity = args[0] if args else 0
        valid_id = (reply.signature() == "u" and len(args) == 1 and isinstance(identity, int)
                    and not isinstance(identity, bool) and 0 < identity < 2 ** 32)
        # A sent D-Bus request cannot be recalled. Keep this one bounded watcher
        # until completion so a late reply after queue-clear/mode-change can
        # close its own card, without touching a replacement daemon's IDs.
        if (generation != self._generation or self._closed or self._latest is None
                or (owner is not None and owner != self._notification_owner)):
            if reply.type() != error_type and valid_id and bus is not None and owner:
                self._close_owned_notification(bus, owner, identity)
            self._dispatch_latest()
            return
        if reply.type() == error_type:
            self._notification_id = 0
            self._forget_notification_owner()
            self._notification_unavailable("Could not show a desktop notification. Local playback is unaffected.")
        else:
            # Refuse unverifiable IDs rather than later replacing another card.
            if valid_id:
                self._notification_id = identity if identity not in self._closed_notification_ids else 0
                self._announce(f"Desktop notifications — {self._notification_server or NOTIFICATIONS_SERVICE}")
            else:
                self._notification_id = 0
                self._forget_notification_owner()
                self._notification_unavailable("The desktop notification service returned an invalid notification ID.")
        self._dispatch_latest()

    def _notification_unavailable(self, message):
        if self._auto_stage == 2:
            self._auto_stage = 3
            self._send_wayland(self._latest)
            return
        self._report(message)
        self._dispatch_latest()

    def _dispatch_latest(self):
        if (not self._closed and self.mode != "off" and self._latest is not None
                and self._latest != self._last_attempt
                and not self._coalesce.isActive()):
            self._coalesce.start(0)

    def _owner_changed(self, _service, _old_owner, _new_owner):
        # A restarted daemon has a new notification-ID namespace.
        if self._closed or self.mode not in {"auto", "desktop"}:
            return
        self._forget_notification_owner()
        if self.mode == "auto" and any(name in os.environ for name in (
                "GENERIC_PLAYER_OSD_ARGV", "GENERIC_PLAYER_OSD_SOCKET", "GENERIC_PLAYER_OSD_COMMAND")):
            return
        if self._route == "desktop":
            self._generation += 1
            self._cancel_pending_owner()
        if ((_new_owner and (self._route in {"", "desktop"} or self._last_error is not None))
                or (self.mode == "auto" and self._route == "desktop")):
            # A card accepted by the old connection may never have reached a
            # visible daemon. Its dedupe marker must not suppress the current
            # song when the service returns. Keep only that latest snapshot;
            # owner-change events provide recovery without a retry timer.
            self._last_attempt = None
            self._dispatch_latest()

    @staticmethod
    def _close_owned_notification(bus, owner, identity):
        message = QtDBus.QDBusMessage.createMethodCall(
            owner, NOTIFICATIONS_PATH, NOTIFICATIONS_SERVICE, "CloseNotification")
        message.setAutoStartService(False)
        message.setArguments([_variant(identity, "UInt")])
        try:
            bus.asyncCall(message, 500)
        except RuntimeError:
            pass

    def _connect_notification_signals(self, owner):
        self._disconnect_notification_signals()
        listener = _NotificationSignals(self, owner)
        try:
            connected = self._bus.connect(owner, NOTIFICATIONS_PATH, NOTIFICATIONS_SERVICE,
                                          "NotificationClosed", listener.closed)
        except (RuntimeError, TypeError):
            connected = False
        if connected:
            self._notification_listener = listener
            self._notification_listener_bus = self._bus
        else:
            listener.deleteLater()

    def _disconnect_notification_signals(self):
        listener, self._notification_listener = self._notification_listener, None
        bus, self._notification_listener_bus = self._notification_listener_bus, None
        if listener is not None:
            try:
                bus.disconnect(listener._owner, NOTIFICATIONS_PATH, NOTIFICATIONS_SERVICE,
                               "NotificationClosed", listener.closed)
            except (RuntimeError, TypeError):
                pass
            listener.deleteLater()

    def _notification_closed(self, owner, identity):
        if owner != self._notification_owner:
            return
        if len(self._closed_notification_ids) >= 32:
            self._closed_notification_ids.clear()
        self._closed_notification_ids.add(identity)
        if identity == self._notification_id:
            self._notification_id = 0

    def _forget_notification_owner(self):
        self._disconnect_notification_signals()
        self._notification_id = 0
        self._notification_owner = ""
        self._notification_info_owner = ""
        self._notification_server = ""
        self._notification_capabilities = None
        self._closed_notification_ids.clear()

    def _close_notification(self):
        if (QtDBus is not None and self._bus is not None
                and self._notification_owner and self._notification_id):
            self._close_owned_notification(self._bus, self._notification_owner, self._notification_id)
        self._forget_notification_owner()

    def _cancel_pending_owner(self):
        # A Notify reply may still yield an owned card ID that needs closing;
        # owner-discovery calls have no such side effect and can be discarded.
        if self._pending_kind != "notify":
            watcher, self._pending_call = self._pending_call, None
            self._pending_kind = ""
            if watcher is not None:
                watcher.deleteLater()

    def _cancel_transports(self, keep_discovery=False):
        self._socket_deadline.stop()
        self._process_deadline.stop()
        if self._process is not None:
            # Retain one process handle until finished; deleting a live QProcess
            # would wait synchronously and could strand its helper children.
            self._kill_process(self._process)
        self._socket_candidates.clear()
        self._command_queue.clear()
        self._wayland_snapshot = None
        self._failed_socket_paths.clear()
        self._route = ""
        self._auto_stage = 0
        sock, self._socket = self._socket, None
        if sock is not None:
            sock.abort()
            sock.deleteLater()
        self._socket_payload = None
        self._socket_address = ""
        self._cancel_pending_owner()
        if keep_discovery:
            return
        watcher, self._service_watcher = self._service_watcher, None
        if watcher is not None:
            try:
                watcher.serviceOwnerChanged.disconnect(self._owner_changed)
            except (RuntimeError, TypeError):
                pass
            watcher.deleteLater()
        self._bus = None
