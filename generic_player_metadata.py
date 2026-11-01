"""Read optional media tags without importing Qt or retaining tag containers.

Dates keep the precision supplied by the file: a year-only tag stays a year,
not an invented January 1. Text is bounded and made safe for D-Bus and plain
OSD labels here; missing or damaged tags never prevent a file from playing.
"""
from __future__ import annotations

from datetime import date, datetime
import math
import os
import re
from urllib.parse import urlsplit

MAX_METADATA_TEXT = 4096
_CONTROLS = re.compile(r"[\x01-\x1f\x7f-\x9f]")
_SURROGATES = re.compile(r"[\ud800-\udfff]")
_PARTIAL_DATE = re.compile(r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?\Z")
_DATE_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9]{2}:[0-9]{2}"
    r"(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?\Z"
)


def safe_metadata_text(value, limit=MAX_METADATA_TEXT) -> str:
    """Return bounded single-line Unicode text suitable for display and IPC."""
    if value is None or isinstance(value, (bool, bytes, bytearray, dict)):
        return ""
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        return ""
    # Slice before substitutions so malformed, very large tags do not create
    # equally large temporary strings for every UI or desktop-bus update.
    text = text[:max(0, int(limit))].replace("\0", "")
    text = _SURROGATES.sub("\ufffd", text)
    return " ".join(_CONTROLS.sub(" ", text).split())


def _first_value(value):
    """Unwrap the small text/value containers used by Mutagen and Qt."""
    for _ in range(4):
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        elif hasattr(value, "text"):
            value = value.text
        elif hasattr(value, "value") and not callable(value.value):
            value = value.value
        else:
            break
    return value


def normalize_media_date(value) -> str:
    """Validate ISO date text, accepting year/month precision without guessing."""
    try:
        value = _first_value(value)
        # QDate/QDateTime offer Python conversions, avoiding a Qt import in
        # playlist and installer code and preserving available date/time data.
        if hasattr(value, "isValid") and not value.isValid():
            return ""
        if hasattr(value, "toPyDateTime"):
            value = value.toPyDateTime()
        elif hasattr(value, "toPyDate"):
            value = value.toPyDate()
        if isinstance(value, (datetime, date)):
            value = value.isoformat()
        if isinstance(value, str) and len(value) > 64:
            return ""
        text = safe_metadata_text(value, 64)
        if _PARTIAL_DATE.fullmatch(text):
            year = int(text[:4])
            if not 1 <= year <= 9999:
                return ""
            if len(text) == 7 and not 1 <= int(text[5:7]) <= 12:
                return ""
            if len(text) == 10:
                date.fromisoformat(text)
            return text
        if _DATE_TIME.fullmatch(text):
            text = text[:10] + "T" + text[11:]
            if text.endswith(("z", "Z")):
                text = text[:-1] + "Z"
            datetime.fromisoformat(text.replace("Z", "+00:00"))
            return text
    except (AttributeError, TypeError, ValueError, OverflowError):
        pass
    return ""


def normalize_year(value) -> str:
    """Extract a year only from a valid, explicitly supplied ISO date/year."""
    normalized = normalize_media_date(value)
    return normalized[:4] if normalized else ""


def _tag_text(tags, *names):
    for name in names:
        try:
            value = tags.get(name)
            text = safe_metadata_text(_first_value(value))
            if text:
                return text
        except Exception:
            # A bad frame is isolated from the other usable fields.
            continue
    return ""


def _tag_date(tags):
    # Prefer the file's ordinary date; original-release tags are a fallback.
    # Raw aliases cover ID3 (including WAV/AIFF), MP4, ASF and APE/Vorbis.
    for name in ("date", "TDRC", "\xa9day", "WM/Year", "year", "Year", "TYER",
                 "originaldate", "TDOR", "originalyear", "TORY"):
        try:
            value = normalize_media_date(tags.get(name))
            if value:
                return value
        except Exception:
            continue
    return ""


def build_media_metadata(file_path, mtype="audio") -> dict:
    """Read one local tag container, with filename/host fallbacks for missing tags."""
    source = os.fspath(file_path) if file_path is not None else ""
    title = os.path.splitext(os.path.basename(source))[0]
    artist, album, media_date, duration_ms = "", "", "", 0
    if mtype == "stream":
        try:
            title = urlsplit(source).hostname or source
        except ValueError:
            title = source
    else:
        for separator in (" — ", " - ", " – ", "_-"):
            if separator in title:
                artist, title = [part.strip() for part in title.split(separator, 1)]
                break
    if mtype == "audio":
        try:
            from mutagen import File as MutagenFile
            # Reading raw tags once also handles WAV ID3 and MP4 atoms; an
            # easy=True read followed by a raw fallback parses artwork twice.
            media = MutagenFile(source, easy=False)
            if media is not None:
                tags = getattr(media, "tags", None)
                if tags is not None:
                    title = _tag_text(tags, "title", "TIT2", "\xa9nam", "Title") or title
                    artist = _tag_text(tags, "artist", "TPE1", "\xa9ART", "Author",
                                       "albumartist", "TPE2", "aART", "WM/AlbumArtist") or artist
                    album = _tag_text(tags, "album", "TALB", "\xa9alb", "WM/AlbumTitle")
                    media_date = _tag_date(tags)
                seconds = float(getattr(getattr(media, "info", None), "length", 0))
                if math.isfinite(seconds):
                    duration_ms = max(0, min(2 ** 31 - 1, int(seconds * 1000)))
        except Exception:
            # Metadata is supplementary; corrupt headers or optional tag
            # dependencies cannot prevent playback through Qt's decoder.
            pass
    title = safe_metadata_text(title) or "Unknown track"
    artist, album = safe_metadata_text(artist), safe_metadata_text(album)
    return {"artist": artist, "title": title, "album": album,
            "date": media_date, "year": media_date[:4], "duration_ms": duration_ms,
            "display": safe_metadata_text(f"{artist} — {title}" if artist else title)}


def read_player_metadata(player, using_qt6) -> dict:
    """Return available Qt metadata changes, leaving absent fields untouched.

    Call only for the currently loaded source. The window owns source identity
    and decides whether a signal belongs to a new radio song or a local file.
    """
    try:
        if using_qt6:
            metadata = player.metaData()
            keys = type(metadata).Key

            def get(name):
                key = getattr(keys, name, None)
                return metadata.value(key) if key is not None else None
        else:
            get = player.metaData
    except (AttributeError, TypeError, RuntimeError):
        return {}

    def first_text(*names):
        for name in names:
            try:
                text = safe_metadata_text(_first_value(get(name)))
                if text:
                    return text
            except (AttributeError, TypeError, RuntimeError, ValueError):
                continue
        return ""

    changes = {}
    for field, names in (("title", ("Title",)),
                         ("artist", ("ContributingArtist", "AlbumArtist", "Author")),
                         ("album", ("AlbumTitle",))):
        value = first_text(*names)
        if value:
            changes[field] = value
    for name in ("Date", "Year"):
        try:
            value = normalize_media_date(get(name))
        except (AttributeError, TypeError, RuntimeError, ValueError):
            continue
        if value:
            changes.update(date=value, year=value[:4])
            break
    return changes
