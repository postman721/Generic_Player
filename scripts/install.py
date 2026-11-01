#!/usr/bin/env python3
"""Install explicitly owned files using a content-and-mode ownership manifest.

Logical destinations remain independent of DESTDIR staging. Installation
preflights the complete payload, then rechecks each destination before changing
it. Uninstall relies on the recorded payload rather than the current checkout,
so modified or unrelated files are preserved across source versions.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ".install-manifest.json"
PACKAGE_MARKER = ".deb-package"
APPLICATION = "generic-player"
APPLICATION_MODULES = (
    "generic_player.py",
    "generic_player_library.py",
    "generic_player_lyrics.py",
    "generic_player_metadata.py",
    "generic_player_mpris.py",
    "generic_player_osd.py",
    "generic_player_qt.py",
    "generic_player_queue.py",
    "generic_player_settings.py",
    "generic_player_ui.py",
    "generic_player_version.py",
    "generic_player_visualizer.py",
    "generic_player_widgets.py",
    "theme.py",
)
_UNCHECKED = object()
DATA_FILES = {
    "applications/generic-player.desktop": "applications/generic-player.desktop",
    "icons/hicolor/scalable/apps/generic-player.svg": "icons/hicolor/scalable/apps/generic-player.svg",
    "man/man1/generic-player.1": "man/man1/generic-player.1.gz",
    "metainfo/net.techtimejourney.GenericPlayer.metainfo.xml": "metainfo/net.techtimejourney.GenericPlayer.metainfo.xml",
}


class InstallError(RuntimeError):
    pass


def absolute_path(value, label):
    if not isinstance(value, str):
        raise InstallError(f"{label} must be a normalized absolute path, without '..'.")
    path = PurePosixPath(value)
    # PurePosixPath preserves a double leading slash, which would otherwise
    # bypass comparisons against the filesystem root used by Layout.
    if (not value or value.startswith("//") or not path.is_absolute() or str(path) != value
            or ".." in path.parts or any(ord(char) < 32 for char in value)):
        raise InstallError(f"{label} must be a normalized absolute path, without '..'.")
    return path


class Layout:
    """Validate logical install roots and map them into an optional staging tree."""
    def __init__(self, prefix="/usr/local", appdir="/opt/generic-player", destdir="", python="/usr/bin/python3"):
        self.prefix = absolute_path(prefix, "PREFIX")
        self.appdir = absolute_path(appdir, "APPDIR")
        self.python = absolute_path(python, "INSTALL_PYTHON")
        if self.prefix == PurePosixPath("/"):
            raise InstallError("PREFIX cannot be the filesystem root.")
        if self.appdir.name != APPLICATION or self.appdir.parent == PurePosixPath("/"):
            raise InstallError("APPDIR must be a dedicated absolute directory ending in /generic-player.")
        self.destdir = absolute_path(destdir, "DESTDIR") if destdir else PurePosixPath("/")
        for shared in (self.prefix / "bin", self.prefix / "share"):
            if self.appdir == shared or self.appdir in shared.parents or shared in self.appdir.parents:
                raise InstallError("APPDIR must not overlap PREFIX/bin or PREFIX/share.")

    def path(self, logical):
        logical = absolute_path(str(logical), "Installed path")
        return Path(self.destdir) / str(logical).lstrip("/")

    @property
    def manifest(self):
        return self.appdir / MANIFEST

    def allowed_file(self, logical):
        logical = absolute_path(logical, "Manifest path")
        if logical.parent == self.appdir:
            return logical.name in {"theme.py", "LICENSE"} or bool(re.fullmatch(r"generic_player(?:_[a-z0-9]+)*\.py", logical.name))
        if logical == self.appdir / "assets/generic-player.svg":
            return True
        external = {self.prefix / "bin/generic-player"}
        external.update(self.prefix / "share" / name for name in DATA_FILES.values())
        external.update(self.prefix / "share/doc/generic-player" / name for name in ("README.md", "copyright"))
        return logical in external


def digest(data):
    return hashlib.sha256(data).hexdigest()


def payload(layout):
    """Describe the shared manual/Debian payload without modifying destinations."""
    files = {}
    # A glob alone could silently omit a lost module and still install a
    # launcher. Validate the complete bundled application before any writes.
    for name in APPLICATION_MODULES:
        source = ROOT / name
        if not source.is_file() or source.is_symlink():
            raise InstallError(f"Incomplete source: missing or non-regular application module: {source}")
    modules = sorted(ROOT.glob("generic_player*.py")) + [ROOT / "theme.py"]
    for source in modules:
        destination = layout.appdir / source.name
        if not layout.allowed_file(str(destination)) or source.is_symlink():
            raise InstallError(f"Unexpected application source: {source}")
        files[str(destination)] = (source.read_bytes(), 0o644)
    for relative in ("LICENSE", "assets/generic-player.svg"):
        files[str(layout.appdir / relative)] = ((ROOT / relative).read_bytes(), 0o644)
    launcher = ("#!/bin/sh\n# Installed by Generic Player; run independently of the current directory.\n"
                f"exec {shlex.quote(str(layout.python))} -B {shlex.quote(str(layout.appdir / 'generic_player.py'))} \"$@\"\n")
    files[str(layout.prefix / "bin/generic-player")] = (launcher.encode(), 0o755)
    for source_name, destination_name in DATA_FILES.items():
        source = ROOT / "data" / source_name
        if source.is_symlink():
            raise InstallError(f"Desktop data must be regular source files: {source}")
        data = source.read_bytes()
        if destination_name.endswith(".gz"):
            data = gzip.compress(data, mtime=0)
        files[str(layout.prefix / "share" / destination_name)] = (data, 0o644)
    for source_name, target_name in (("README.md", "README.md"), ("LICENSE", "copyright")):
        files[str(layout.prefix / "share/doc/generic-player" / target_name)] = ((ROOT / source_name).read_bytes(), 0o644)
    return files


def check_ancestors(path):
    for parent in reversed(path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise InstallError(f"Refusing non-directory or symlink ancestor: {parent}")


def file_state(path):
    check_ancestors(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise InstallError(f"Refusing symlink, non-regular, or multiply linked file: {path}")
    # A modified destination or corrupt manifest may be much larger than our
    # source files. Ownership checks must not retain the whole file in memory.
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(128 * 1024):
            checksum.update(chunk)
    return {"sha256": checksum.hexdigest(), "mode": stat.S_IMODE(info.st_mode)}


def require_file_state(layout, logical, expected):
    """Do not act on a destination that changed since its ownership check."""
    path = layout.path(logical)
    if file_state(path) != expected:
        raise InstallError(f"Destination changed during operation; refusing to modify: {path}")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InstallError("Ownership manifest contains duplicate keys.")
        result[key] = value
    return result


def load_manifest(layout):
    path = layout.path(layout.manifest)
    if file_state(path) is None:
        return None
    if path.stat().st_size > 1024 * 1024:
        raise InstallError("Ownership manifest is unexpectedly large; nothing changed.")
    try:
        record = json.loads(path.read_text(), object_pairs_hook=_unique_pairs)
        if (record["application"] != APPLICATION or record["schema"] != 1
                or record["prefix"] != str(layout.prefix) or record["appdir"] != str(layout.appdir)
                or not isinstance(record["files"], dict) or not isinstance(record["directories"], list)):
            raise ValueError
        for name, state in record["files"].items():
            if (not layout.allowed_file(name) or not isinstance(state, dict)
                    or set(state) != {"sha256", "mode"}
                    or not isinstance(state["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", state["sha256"])
                    or type(state["mode"]) is not int or state["mode"] not in (0o644, 0o755)):
                raise ValueError
        parents = {str(parent) for name in [*record["files"], str(layout.manifest)]
                   for parent in PurePosixPath(name).parents if str(parent) != "/"}
        if any(not isinstance(name, str) or name not in parents for name in record["directories"]):
            raise ValueError
    except (KeyError, TypeError, ValueError, RecursionError) as error:
        raise InstallError("Invalid or mismatched ownership manifest; nothing changed.") from error
    return record


def ensure_parent(layout, logical, created):
    target = layout.path(logical)
    check_ancestors(target)
    missing = []
    for parent in target.parents:
        if parent.exists():
            break
        missing.append(parent)
    for parent in reversed(missing):
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)  # A restrictive invoking umask must not hide installed code.
        # DESTDIR itself is a staging boundary, never an owned target directory.
        if parent != Path(layout.destdir) and Path(layout.destdir) in parent.parents:
            created.add("/" + parent.relative_to(layout.destdir).as_posix())
    return target


def write_atomic(layout, logical, data, mode, created, expected=_UNCHECKED):
    target = ensure_parent(layout, logical, created)
    descriptor, temporary = tempfile.mkstemp(prefix=".generic-player-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), mode)
        if expected is not _UNCHECKED:
            require_file_state(layout, logical, expected)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check_package_boundary(layout):
    path = layout.path(layout.appdir / PACKAGE_MARKER)
    check_ancestors(path)
    if path.exists() or path.is_symlink():
        raise InstallError("This is a Debian-package installation. Manage it with apt/dpkg, not make install/uninstall.")


def install(layout):
    """Preflight ownership, apply changes, and roll back ordinary I/O failures."""
    check_package_boundary(layout)
    files = payload(layout)
    manifest_state = file_state(layout.path(layout.manifest))
    previous = load_manifest(layout)
    require_file_state(layout, layout.manifest, manifest_state)
    owned = previous["files"] if previous else {}
    # Inspect every write/removal before changing even the first destination.
    inspected = {}
    for name in sorted(set(files) | set(owned)):
        current = file_state(layout.path(name))
        if current is not None and (name not in owned or current != owned[name]):
            raise InstallError(f"Existing file is unowned or modified; nothing changed: {layout.path(name)}")
        inspected[name] = current
    original_directories = set(previous["directories"] if previous else [])
    created = set(original_directories)
    changed = []
    try:
        for name, (data, mode) in files.items():
            current = inspected[name]
            require_file_state(layout, name, current)
            expected = {"sha256": digest(data), "mode": mode}
            if current != expected:
                before = (layout.path(name).read_bytes(), current["mode"]) if current else None
                write_atomic(layout, name, data, mode, created, current)
                changed.append((name, before, expected))
        for name in set(owned) - set(files):
            path = layout.path(name)
            require_file_state(layout, name, inspected[name])
            if path.exists():
                before = (path.read_bytes(), owned[name]["mode"])
                path.unlink()
                changed.append((name, before, None))
        record = {"application": APPLICATION, "schema": 1, "prefix": str(layout.prefix),
                  "appdir": str(layout.appdir), "files": {name: {"sha256": digest(data), "mode": mode}
                                                            for name, (data, mode) in files.items()},
                  "directories": sorted(created)}
        ensure_parent(layout, layout.manifest, created)
        record["directories"] = sorted(created)
        write_atomic(layout, layout.manifest, (json.dumps(record, indent=2, sort_keys=True) + "\n").encode(), 0o644, created, manifest_state)
    except (OSError, InstallError) as error:
        rollback_errors = []
        for name, before, expected in reversed(changed):
            try:
                if file_state(layout.path(name)) != expected:
                    raise InstallError("destination changed during the operation")
                if before is None:
                    layout.path(name).unlink()
                else:
                    write_atomic(layout, name, *before, created, expected)
            except (OSError, InstallError) as failure:
                rollback_errors.append(f"{layout.path(name)}: {failure}")
        for name in sorted(created - original_directories, key=lambda item: len(PurePosixPath(item).parts), reverse=True):
            try:
                layout.path(name).rmdir()
            except OSError:
                pass
        if rollback_errors:
            raise InstallError(f"Install failed: {error}. Some owned changes could not be restored: "
                               + "; ".join(rollback_errors)) from error
        raise InstallError(f"Install failed; completed file changes were rolled back: {error}") from error
    print(f"Installed {len(files)} owned files. Launcher: {layout.path(layout.prefix / 'bin/generic-player')}")
    return 0


def uninstall(layout):
    """Remove unchanged owned files; retain a manifest for modified survivors."""
    check_package_boundary(layout)
    manifest_state = file_state(layout.path(layout.manifest))
    previous = load_manifest(layout)
    require_file_state(layout, layout.manifest, manifest_state)
    if previous is None:
        print("No manual Generic Player ownership manifest found; nothing removed.")
        return 0
    unchanged, preserved = [], []
    # Validate every location before deleting anything. Symlinks abort the operation.
    for name, expected in previous["files"].items():
        current = file_state(layout.path(name))
        if current is None:
            continue
        (unchanged if current == expected else preserved).append(name)
    for name in previous["directories"]:
        path = layout.path(name)
        check_ancestors(path / "unused")
    for name in unchanged:
        require_file_state(layout, layout.manifest, manifest_state)
        require_file_state(layout, name, previous["files"][name])
        layout.path(name).unlink()
    if preserved:
        previous["files"] = {name: previous["files"][name] for name in preserved}
        # Keep ancestor ownership so a later uninstall can finish after restoration.
        parents = {str(parent) for name in [*preserved, str(layout.manifest)] for parent in PurePosixPath(name).parents}
        for name in sorted(set(previous["directories"]) - parents, key=lambda item: len(PurePosixPath(item).parts), reverse=True):
            try:
                layout.path(name).rmdir()
            except OSError:
                pass
        previous["directories"] = [name for name in previous["directories"] if name in parents]
        write_atomic(layout, layout.manifest, (json.dumps(previous, indent=2, sort_keys=True) + "\n").encode(), 0o644, set(), manifest_state)
        print("Preserved modified owned files (and their ownership manifest):", file=sys.stderr)
        for name in preserved:
            print("  " + str(layout.path(name)), file=sys.stderr)
        print("Restore the recorded version before retrying, or manage these files explicitly.", file=sys.stderr)
        return 1
    require_file_state(layout, layout.manifest, manifest_state)
    layout.path(layout.manifest).unlink()
    for name in sorted(previous["directories"], key=lambda item: len(PurePosixPath(item).parts), reverse=True):
        try:
            layout.path(name).rmdir()
        except OSError:
            pass  # A directory with unrelated contents must remain untouched.
    print(f"Removed {len(unchanged)} unchanged owned files. User settings and unrelated files were preserved.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    args = parser.parse_args(argv)
    try:
        layout = Layout(os.environ.get("PREFIX", "/usr/local"), os.environ.get("APPDIR", "/opt/generic-player"),
                        os.environ.get("DESTDIR", ""), os.environ.get("INSTALL_PYTHON", "/usr/bin/python3"))
        return install(layout) if args.action == "install" else uninstall(layout)
    except (InstallError, OSError) as error:
        print(f"Generic Player {args.action}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
