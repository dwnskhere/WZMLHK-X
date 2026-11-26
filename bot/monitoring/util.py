"""Small pure helpers shared across the monitoring package."""
import re
from datetime import datetime, timezone

_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4, "PB": 1024**5}
_SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([KMGTP]?)i?B(?:/s)?\s*$", re.I)
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def utc_now_iso(ts=None):
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def num(value, default=None):
    """Return a finite float/int or ``default``. Booleans are not numbers here."""
    if value is None or isinstance(value, bool):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if out != out or out in (float("inf"), float("-inf")):
        return default
    return int(out) if out.is_integer() else out


def to_int(value, default=None):
    out = num(value, None)
    return default if out is None else int(out)


def parse_size(text):
    """'12.50MB' / '3.1KB/s' -> bytes. Returns None when not parseable."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return num(text)
    m = _SIZE_RE.match(str(text or ""))
    if not m:
        return None
    return int(float(m.group(1)) * _UNITS[(m.group(2).upper() + "B")])


def parse_percent(text):
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return num(text)
    try:
        return num(str(text).strip().rstrip("%"))
    except Exception:
        return None


def clean_text(value, limit=300):
    """Printable, bounded text for names/messages."""
    if value is None:
        return None
    text = _CTRL.sub(" ", str(value)).strip()
    return text[:limit]


def pct(part, whole):
    try:
        if not whole:
            return None
        return round(min(max(part / whole * 100, 0), 100), 2)
    except Exception:
        return None


def camel_to_snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
