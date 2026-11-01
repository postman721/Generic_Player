"""Validate media entries and read/write playlists without importing Qt.

Imported paths become absolute while missing files remain recoverable queue
entries. File size and entry limits apply before data reaches the UI; writes
replace the previous file only after a complete, bounded serialization.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import uuid
from collections.abc import Iterable
from itertools import chain
from pathlib import Path
from urllib.parse import unquote, urlsplit

from generic_player_metadata import normalize_media_date, normalize_year, safe_metadata_text

AUDIO_EXTENSIONS = frozenset({
    ".mp3", ".ogg", ".oga", ".flac", ".wav", ".m4a", ".aac", ".opus",
    ".aiff", ".aif", ".wma", ".alac", ".ape",
})
VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".webm", ".m4v",
    ".mpeg", ".mpg", ".ts", ".ogv",
})
PLAYLIST_EXTENSIONS = frozenset({".json", ".m3u", ".m3u8"})
MAX_PLAYLIST_BYTES = 8 * 1024 * 1024
MAX_PLAYLIST_ENTRIES = 50000
_M3U_LINE_BREAKS = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")


def validate_stream_url(value: str) -> str:
    """Accept an absolute HTTP(S) media URL, rejecting malformed addresses."""
    if not isinstance(value, str):
        raise ValueError("A stream URL must be text.")
    value = value.strip()
    if not value or any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise ValueError("Enter a complete HTTP or HTTPS URL without spaces.")
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        parsed.port
    except ValueError:
        raise ValueError("Enter a valid HTTP or HTTPS stream URL.") from None
    return value


def media_type(path: str) -> str | None:
    """Classify a filename by its supported extension, without filesystem I/O."""
    extension = os.path.splitext(path)[1].lower()
    if not extension:
        name = os.path.basename(path)
        if name.startswith("..") or name in {"", "."}:
            # pathlib accepts names such as '..mp3' and ignores trailing '/.'.
            # Keep that behavior without creating Path objects for every
            # unsupported file during a large folder import.
            extension = Path(path).suffix.lower()
    if extension in AUDIO_EXTENSIONS:
        return "audio"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    return None


def decode_local_file_url(value: str) -> str:
    """Decode an unambiguous local file URL without loading graphical modules."""
    if not isinstance(value, str) or not value.lower().startswith("file:"):
        raise ValueError("A local file URL must use the file scheme.")
    # urlsplit silently drops raw tabs/newlines. Check the original input so
    # malformed URLs cannot accidentally identify a different local file.
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("File URLs cannot contain literal control characters.")
    parsed = urlsplit(value)
    if (parsed.netloc.lower() not in {"", "localhost"}
            or not parsed.path.startswith("/") or parsed.query or parsed.fragment):
        raise ValueError("File URLs must identify an absolute path on this computer.")
    path = unquote(parsed.path, encoding="utf-8", errors="strict")
    if any(character in path for character in ("\0", "\n", "\r")):
        raise ValueError("A local file path cannot contain NUL or line breaks.")
    return path


def _local_path(value: str, base_dir: str | os.PathLike | None = None) -> str:
    if value.lower().startswith("file:"):
        value = decode_local_file_url(value)
    if not value or "\0" in value or "\n" in value or "\r" in value:
        raise ValueError("A local media path cannot be empty or contain line breaks.")
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(base_dir or os.getcwd()) / path
        return str(path.resolve(strict=False))
    except RuntimeError as exc:
        # Older supported Python versions raise RuntimeError for symlink loops
        # and unresolved home directories; surface them as invalid input.
        raise ValueError(f"Could not resolve the local media path: {exc}") from exc


def normalize_entry(entry: dict, base_dir: str | os.PathLike | None = None) -> dict:
    """Return a safe, independent entry; missing local files remain in saved queues."""
    if not isinstance(entry, dict):
        raise ValueError("Every playlist item must be an object.")
    source, kind = entry.get("path"), entry.get("type")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Every playlist item needs a non-empty path.")
    if not isinstance(kind, str) or kind not in {"audio", "video", "stream"}:
        raise ValueError("Playlist item type must be audio, video, or stream.")
    if kind == "stream":
        source = validate_stream_url(source)
    else:
        if "://" in source and not source.lower().startswith("file:"):
            raise ValueError("Internet URLs must use the stream item type.")
        source = _local_path(source, base_dir)
        kind = media_type(source)
        if kind is None:
            raise ValueError(f"Unsupported media filename: {Path(source).name}")
    result = {"path": source, "type": kind}
    for field in ("artist", "title", "album", "display"):
        value = entry.get(field, "")
        if not isinstance(value, str):
            raise ValueError(f"Playlist {field} must be text.")
        result[field] = safe_metadata_text(value)
    if not result["title"]:
        result["title"] = safe_metadata_text(urlsplit(source).hostname if kind == "stream" else Path(source).stem)
    if not result["display"]:
        result["display"] = safe_metadata_text(f'{result["artist"]} — {result["title"]}' if result["artist"] else result["title"])
    # Preserve dates through JSON/session round trips without inventing a day
    # for year-only tags. An invalid optional date cannot break a valid queue.
    media_date = normalize_media_date(entry.get("date"))
    year = media_date[:4] if media_date else normalize_year(entry.get("year"))
    result["date"], result["year"] = media_date or year, year
    identity = entry.get("id")
    result["id"] = identity if isinstance(identity, str) and identity else uuid.uuid4().hex
    duration = entry.get("duration_ms", 0)
    if isinstance(duration, int) and not isinstance(duration, bool):
        result["duration_ms"] = max(0, min(2 ** 31 - 1, duration))
    elif isinstance(duration, float) and math.isfinite(duration):
        result["duration_ms"] = max(0, min(2 ** 31 - 1, int(duration)))
    else:
        result["duration_ms"] = 0
    return result


def normalize_playlist(data, base_dir: str | os.PathLike | None = None) -> list[dict]:
    """Validate a JSON-style array and give each queue occurrence its own ID."""
    if not isinstance(data, list):
        raise ValueError("The playlist must be a JSON array of media items.")
    if len(data) > MAX_PLAYLIST_ENTRIES:
        raise ValueError(f"Playlists may contain at most {MAX_PLAYLIST_ENTRIES:,} items.")
    return _normalize_entries(data, base_dir)


def _normalize_entries(data, base_dir) -> list[dict]:
    """Normalize already count-limited input, retaining only the final entries."""
    entries, identities = [], set()
    for index, item in enumerate(data):
        try:
            entry = normalize_entry(item, base_dir)
        except (ValueError, TypeError, OSError) as exc:
            raise ValueError(f"Item {index + 1}: {exc}") from exc
        # Repeated paths are allowed: IDs distinguish occurrences for playback
        # and desktop controls, even when a saved playlist reused an identifier.
        if entry["id"] in identities:
            entry["id"] = uuid.uuid4().hex
        identities.add(entry["id"])
        entries.append(entry)
    return entries


def load_playlist_file(filename: str | os.PathLike) -> list[dict]:
    """Load bounded UTF-8 JSON/M3U and resolve relative paths beside the file."""
    path = Path(filename)
    text = read_bounded_text(path, MAX_PLAYLIST_BYTES, encoding="utf-8-sig")
    if path.suffix.lower() in {".m3u", ".m3u8"}:
        return _normalize_entries(_m3u_entries(text), path.parent)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON playlist on line {exc.lineno}.") from exc
    except RecursionError as exc:
        raise ValueError("The JSON playlist is too deeply nested.") from exc
    return normalize_playlist(data, path.parent)


def _m3u_entries(text: str):
    """Yield records so large imports do not retain a second list of entries."""
    count, pending_title, pending_duration = 0, "", 0
    for line_number, line in enumerate(_playlist_lines(text), 1):
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF:"):
            pending_title = line.split(",", 1)[1].strip() if "," in line else ""
            try:
                seconds = float(line.split(":", 1)[1].split(",", 1)[0])
                pending_duration = int(max(0, min(2 ** 31 - 1, seconds * 1000))) if math.isfinite(seconds) else 0
            except ValueError:
                pending_duration = 0
            continue
        if line.startswith("#"):
            continue
        if count >= MAX_PLAYLIST_ENTRIES:
            raise ValueError(f"Playlists may contain at most {MAX_PLAYLIST_ENTRIES:,} items.")
        scheme = urlsplit(line).scheme.lower()
        kind = "stream" if scheme in {"http", "https"} else media_type(line)
        if scheme and scheme != "file" and kind != "stream":
            raise ValueError(f"Line {line_number}: unsupported URL scheme.")
        if scheme == "file":
            kind = media_type(decode_local_file_url(line))
        if kind is None:
            raise ValueError(f"Line {line_number}: unsupported media filename.")
        count += 1
        # EXTINF describes the next media line, not comments or blank lines.
        yield {"path": line, "type": kind, "title": pending_title,
               "display": pending_title, "duration_ms": pending_duration}
        pending_title, pending_duration = "", 0


def _playlist_lines(text: str):
    """Iterate splitlines-compatible records without allocating a second list."""
    start = 0
    for separator in _M3U_LINE_BREAKS.finditer(text):
        yield text[start:separator.start()]
        start = separator.end()
    if start < len(text):
        yield text[start:]


def read_bounded_text(filename: str | os.PathLike, max_bytes: int, *, encoding="utf-8") -> str:
    """Enforce the limit on bytes actually read, including files that grow."""
    raw = bytearray()
    with open(filename, "rb") as handle:
        while True:
            chunk = handle.read(min(64 * 1024, max_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > max_bytes:
                raise ValueError(f"The file is too large (maximum {max_bytes / (1024 * 1024):g} MiB).")
    return raw.decode(encoding)


def atomic_write_text(filename: str | os.PathLike, text: str | Iterable[str], *, max_bytes: int | None = None) -> None:
    """Stream UTF-8 chunks and preserve the old file if writing/encoding fails.

    Stage beside the destination so os.replace stays on the same filesystem.
    Flush the staged contents before publishing; this does not promise directory
    metadata durability after a system crash.
    """
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            size = 0
            for chunk in (text,) if isinstance(text, str) else text:
                if max_bytes is not None:
                    # Character count is a cheap byte-length lower bound and avoids
                    # encoding a single enormous metadata field unnecessarily.
                    if len(chunk) > max_bytes - size:
                        raise ValueError(f"The file is too large (maximum {max_bytes / (1024 * 1024):g} MiB).")
                    size += len(chunk.encode("utf-8"))
                    if size > max_bytes:
                        raise ValueError(f"The file is too large (maximum {max_bytes / (1024 * 1024):g} MiB).")
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def save_playlist_file(filename: str | os.PathLike, entries: list[dict]) -> None:
    """Validate a snapshot, then serialize it without building a full text copy."""
    path = Path(filename)
    entries = normalize_playlist(entries, path.parent)
    if path.suffix.lower() in {".m3u", ".m3u8"}:
        text = _m3u_chunks(entries, path.parent.resolve())
    else:
        text = chain(json.JSONEncoder(ensure_ascii=False, indent=2).iterencode(entries), ("\n",))
    atomic_write_text(path, text, max_bytes=MAX_PLAYLIST_BYTES)


def _m3u_chunks(entries, base_dir):
    yield "#EXTM3U\n"
    for entry in entries:
        title = _M3U_LINE_BREAKS.sub(" ", entry["display"])
        seconds = entry["duration_ms"] // 1000 if entry["duration_ms"] else -1
        yield f"#EXTINF:{seconds},{title}\n"
        source = entry["path"]
        if entry["type"] != "stream":
            if _M3U_LINE_BREAKS.search(source):
                # These are legal local filename characters but delimit M3U
                # records. A file URI round-trips them without adding tracks.
                yield Path(source).as_uri() + "\n"
                continue
            try:
                source = str(Path(source).relative_to(base_dir))
            except ValueError:
                pass
            # A relative filename must not be mistaken for a comment or
            # URL scheme, or lose a leading space when the M3U is read.
            if not Path(source).is_absolute() and (
                    source.startswith("#") or source != source.lstrip()
                    or urlsplit(source).scheme):
                source = "./" + source
        yield source + "\n"


def _media_candidates(source):
    if source.is_dir():
        for directory, directories, files in os.walk(source, followlinks=False):
            directories[:] = sorted(name for name in directories if not name.startswith("."))
            files.sort()
            for name in files:
                yield os.path.join(directory, name)
    else:
        yield str(source)


def collect_media_paths(paths) -> tuple[list[str], int]:
    """Return unique resolved media paths and a count of unusable candidates.

    Walk each directory in name order, skip hidden subdirectories, and avoid
    following directory symlinks. A bad file does not discard its valid siblings;
    duplicate paths are ignored rather than reported as failed imports.
    """
    result, seen, skipped = [], set(), 0
    for value in paths:
        try:
            source = Path(value).expanduser()
            for candidate in _media_candidates(source):
                try:
                    if media_type(candidate) is None:
                        skipped += 1
                        continue
                    candidate = Path(candidate)
                    if not candidate.is_file():
                        skipped += 1
                        continue
                    normalized = str(candidate.resolve())
                except (OSError, TypeError, ValueError, RuntimeError):
                    skipped += 1
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                result.append(normalized)
        except (OSError, TypeError, ValueError, RuntimeError):
            skipped += 1
    return result, skipped
