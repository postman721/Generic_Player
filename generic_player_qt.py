"""Choose one Qt binding, including its multimedia backend, for the whole app."""

import importlib
import os
import sys


def _select_binding():
    requested = os.environ.get("GENERIC_PLAYER_QT_API", "").strip()
    if requested not in ("", "5", "6"):
        raise RuntimeError("GENERIC_PLAYER_QT_API must be 5 or 6.")
    candidates = [requested] if requested else ["6", "5"]
    # An embedding application must never mix widgets from two Qt runtimes.
    for version in ("6", "5"):
        if f"PyQt{version}.QtWidgets" in sys.modules:
            candidates = [version]
            break
    failures = []
    for version in candidates:
        try:
            modules = tuple(importlib.import_module(f"PyQt{version}.{name}") for name in
                            ("QtCore", "QtGui", "QtWidgets", "QtMultimedia", "QtMultimediaWidgets"))
            return version == "6", modules
        except ImportError as error:
            failures.append(f"PyQt{version}: {error}")
    raise ImportError("Generic Player needs PyQt6 or PyQt5 with Qt Multimedia. "
                      "See README.md for installation instructions.\n" + "\n".join(failures))


USING_QT6, (QtCore, QtGui, QtWidgets, QtMultimedia, QtMultimediaWidgets) = _select_binding()
Qt = QtCore.Qt
QAction = QtGui.QAction if USING_QT6 else QtWidgets.QAction


def stop_media_player(player):
    """Stop on the caller's GUI thread while native callbacks can acquire GIL.

    This is a synchronous stop, not a queued request; callers can safely clear
    the source and detach outputs after it returns.
    """
    if isinstance(player, QtMultimedia.QMediaPlayer):
        # Some PyQt bindings retain the GIL around QMediaPlayer.stop(). FFmpeg
        # can wait for a decoder callback that needs that same lock. The Qt
        # meta-call binding releases it, while DirectConnection still executes
        # the native slot immediately on the caller's (GUI) thread.
        direct = Qt.ConnectionType.DirectConnection if USING_QT6 else Qt.DirectConnection
        QtCore.QMetaObject.invokeMethod(player, "stop", direct)
    else:
        # Preserve the small backend-adapter interface used by embedders.
        player.stop()
