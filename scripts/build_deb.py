#!/usr/bin/env python3
"""Build a rootless Debian package from a Git checkout or extracted release."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

if __package__:
    from .install import Layout, PACKAGE_MARKER, ROOT, payload
else:
    from install import Layout, PACKAGE_MARKER, ROOT, payload


def package_version():
    text = (ROOT / "generic_player_version.py").read_text()
    version = re.search(r'^__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', text, re.MULTILINE)
    revision = os.environ.get("PACKAGE_REVISION", "1")
    if not version or not re.fullmatch(r"[1-9][0-9]*", revision):
        raise ValueError("The source version must be x.y.z and PACKAGE_REVISION a positive integer.")
    return version.group(1) + "-" + revision


def preinst_script(paths, appdir):
    """Do not let dpkg adopt manual/unowned files or follow install-root symlinks."""
    parents = sorted({str(parent) for name in paths for parent in PurePosixPath(name).parents if str(parent) != "/"})
    quoted_parents = " ".join(shlex.quote(name) for name in parents)
    quoted_paths = " ".join(shlex.quote(name) for name in sorted(paths))
    manifest = shlex.quote(str(appdir / ".install-manifest.json"))
    return f'''#!/bin/sh
set -eu
LC_ALL=C
export LC_ALL
case "${{1:-}}" in install|upgrade) ;; *) exit 0 ;; esac
if [ -e {manifest} ] || [ -L {manifest} ]; then
    echo "Generic Player: remove the manual installation with its make uninstall before installing this package." >&2
    exit 1
fi
for parent in {quoted_parents}; do
    if [ -L "$parent" ] || {{ [ -e "$parent" ] && [ ! -d "$parent" ]; }}; then
        echo "Generic Player: refusing non-directory or symlink installation parent: $parent" >&2
        exit 1
    fi
done
for target in {quoted_paths}; do
    if [ -e "$target" ] || [ -L "$target" ]; then
        owner=$(dpkg-query --search "$target" 2>/dev/null) || owner=
        if [ "$owner" != "generic-player: $target" ]; then
            echo "Generic Player: refusing to overwrite a file not owned by this package: $target" >&2
            exit 1
        fi
    fi
done
exit 0
'''


def build():
    if not shutil.which("dpkg-deb"):
        raise RuntimeError("dpkg-deb is required. Install the distribution's dpkg package.")
    version = package_version()
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / f"generic-player_{version}_all.deb"
    # Debian packages always use the system prefix and interpreter, regardless
    # of manual-install overrides in the invoking shell or Make command.
    layout = Layout(prefix="/usr", appdir="/opt/generic-player", python="/usr/bin/python3")
    files = payload(layout)
    marker = str(layout.appdir / PACKAGE_MARKER)
    files[marker] = (f"generic-player {version}\nManaged by dpkg. Remove with apt/dpkg.\n".encode(), 0o644)
    with tempfile.TemporaryDirectory(prefix=".deb-stage-", dir=dist) as temporary:
        temporary = Path(temporary)
        environment = dict(os.environ, TMPDIR=str(temporary))
        stage = temporary / "root"
        stage.mkdir(mode=0o755)
        # mkdir's mode is filtered by the caller's umask. Package directory
        # modes must be stable even when the build user's umask is 077.
        stage.chmod(0o755)
        for name, (data, mode) in files.items():
            path = stage / name.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(mode)
        for path in stage.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
        control_dir = stage / "DEBIAN"
        control_dir.mkdir(mode=0o755)
        control_dir.chmod(0o755)
        installed_size = sum(len(data) for data, _ in files.values()) // 1024 + 1
        control = f'''Package: generic-player
Version: {version}
Section: sound
Priority: optional
Architecture: all
Maintainer: JJ Posti <jjposti@techtimejourney.net>
Installed-Size: {installed_size}
Depends: python3 (>= 3.10), python3-numpy, python3-mutagen, python3-requests, python3-pyqt6, python3-pyqt6.qtmultimedia, libqt6multimediawidgets6
Recommends: ffmpeg, libavcodec-extra
Suggests: playerctl
Homepage: https://github.com/postman721/Generic_Player
Description: Native music and video player with an audio visualizer
 Plays local audio, video and HTTP(S) streams using Qt Multimedia.
 Includes a searchable queue, playlists, themes, lyrics, desktop media
 controls and real decoded-audio visualization on supported Qt backends.
 The code is installed under /opt/generic-player and launched with
 the generic-player command.
'''
        (control_dir / "control").write_text(control)
        (control_dir / "control").chmod(0o644)
        (control_dir / "preinst").write_text(preinst_script(files, layout.appdir))
        (control_dir / "preinst").chmod(0o755)
        sums = "".join(f"{hashlib.md5(data).hexdigest()}  {name.lstrip('/')}\n" for name, (data, _) in sorted(files.items()))
        (control_dir / "md5sums").write_text(sums)
        (control_dir / "md5sums").chmod(0o644)
        candidate = temporary / output.name
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(candidate)], check=True, env=environment)
        extracted = temporary / "verify"
        subprocess.run(["dpkg-deb", "--extract", str(candidate), str(extracted)], check=True, env=environment)
        actual = {"/" + path.relative_to(extracted).as_posix() for path in extracted.rglob("*") if path.is_file()}
        if actual != set(files):
            raise RuntimeError("Debian payload file inventory does not match the source.")
        for name, (data, mode) in files.items():
            path = extracted / name.lstrip("/")
            if path.read_bytes() != data or path.stat().st_mode & 0o777 != mode:
                raise RuntimeError(f"Debian payload content or mode mismatch: {name}")
        os.replace(candidate, output)
    print(output)
    print("Built and verified the Debian payload, file inventory, and permissions.")
    return 0


def main():
    try:
        return build()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Generic Player Debian build: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
