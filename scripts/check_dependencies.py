#!/usr/bin/env python3
"""Check the selected Python interpreter and media dependencies."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def check_optional_osd():
    """Explain discovery without launching a client or contacting a service."""
    print("Optional OSD: Automatic checks running Wayland sockets and the public "
          "desktop notification service at runtime (GNOME, KDE and compatible "
          "daemons). No services are contacted by this check.", flush=True)
    try:
        from generic_player_osd import custom_osd_argv, find_osd_commands
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        print(f"Optional OSD configuration check unavailable: {error}", flush=True)
        return

    # The custom helper validates templates and resolves the executable only.
    # Do not print the remaining arguments: they are user configuration and
    # may contain information that is unrelated to a dependency diagnostic.
    try:
        custom = custom_osd_argv()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Optional custom OSD configuration invalid: {error}", flush=True)
    else:
        if custom:
            print(f"Optional custom OSD command: {custom[0]} "
                  "(configured argument array; takes priority in Automatic).", flush=True)
        else:
            print("Optional custom OSD command: not configured; use "
                  "GENERIC_PLAYER_OSD_ARGV for a tool with another interface.", flush=True)

    try:
        commands = find_osd_commands()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Optional Wayland OSD command discovery unavailable: {error}", flush=True)
    else:
        if commands:
            print("Optional Wayland OSD candidates: " + ", ".join(commands)
                  + " (compatibility checked only when notifications are enabled).", flush=True)
        elif "GENERIC_PLAYER_OSD_COMMAND" in os.environ:
            print("Optional Wayland OSD command: configured override is not a "
                  "usable absolute executable path.", flush=True)
        else:
            print("Optional Wayland OSD command: not found. Checked osd, wayland-osd, "
                  "wayland_osd and wayland-volume-osd in /usr/bin, /usr/local/bin "
                  "and PATH; an existing compatible socket can still be used.", flush=True)

    if "GENERIC_PLAYER_OSD_SOCKET" in os.environ:
        value = os.environ["GENERIC_PLAYER_OSD_SOCKET"]
        try:
            path = Path(value).expanduser()
            if not value or "\0" in value or not path.is_absolute():
                raise ValueError("expected one absolute socket path")
            info = path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError("expected an existing socket owned by the current user")
        except (OSError, RuntimeError, ValueError) as error:
            print(f"Optional OSD socket override unavailable: {error}", flush=True)
        else:
            print(f"Optional OSD socket override: {path} "
                  "(ownership checked; protocol and reachability checked at runtime).", flush=True)
    print("OSD software is optional. Explicit invalid overrides are reported at "
          "runtime without silently switching services; local playback remains available.", flush=True)


def check_dependencies():
    """Use the invoking interpreter so system and virtualenv installs cannot mix."""
    print(f"Python: {sys.version.split()[0]} ({sys.executable})", flush=True)
    if sys.version_info < (3, 10):
        print("Python 3.10 or newer is required.", file=sys.stderr)
        return False
    missing = []
    for module in ("numpy", "mutagen", "requests"):
        try:
            importlib.import_module(module)
        except ImportError as error:
            missing.append(f"{module}: {error}\n  Install this Python module using the "
                           "distribution-specific system packages listed in README.md.")
    try:
        qt = importlib.import_module("generic_player_qt")
        print(f"Qt: {qt.QtCore.QT_VERSION_STR} (PyQt{'6' if qt.USING_QT6 else '5'})", flush=True)
        # Inspect the module only: the check must not connect to or autostart a
        # session bus, and desktop integration is optional for local playback.
        try:
            importlib.import_module(f"PyQt{'6' if qt.USING_QT6 else '5'}.QtDBus")
        except (ImportError, RuntimeError) as error:
            print(f"Desktop controls: QtDBus unavailable ({error}); local playback remains available.", flush=True)
        else:
            print("Desktop controls: QtDBus available; MPRIS requires a desktop session bus at runtime.", flush=True)
    except (ImportError, RuntimeError) as error:
        missing.append(f"Qt Multimedia: {error}\n  Install PyQt6 plus Qt Multimedia, or the "
                       "supported PyQt5 alternative. README.md lists the packages for "
                       "Debian/Ubuntu, Arch, and Fedora.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        missing.append("FFmpeg command not found.\n  See README.md for the distribution's "
                       "FFmpeg package (Fedora uses ffmpeg-free).")
    else:
        try:
            probe = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=10)
            if probe.returncode:
                missing.append(f"FFmpeg could not start: {(probe.stderr or probe.stdout).strip()}")
            else:
                print(f"FFmpeg: {ffmpeg}", flush=True)
        except (OSError, subprocess.TimeoutExpired) as error:
            missing.append(f"FFmpeg could not start: {error}")
    if missing:
        print("\nDependency check failed:\n- " + "\n- ".join(missing), file=sys.stderr)
        print("\nInstall the system dependencies listed in README.md and use the Python "
              "interpreter that can import them. A missing shared library is a runtime "
              "dependency problem; changing file execute permissions does not supply it.", file=sys.stderr)
        return False
    print("Optional tools: playerctl " + ("available" if shutil.which("playerctl") else "not installed")
          + "; OSD software is not required.", flush=True)
    check_optional_osd()
    return True


def main():
    # Resolve imports from this source tree, independent of the caller's directory.
    sys.path.insert(0, str(ROOT))
    if not check_dependencies():
        return 2
    print("Dependency check passed. Playback also requires a graphical desktop, "
          "working audio output, and suitable codecs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
