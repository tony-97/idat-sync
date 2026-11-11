import re
import unicodedata

WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(s: str, replacement: str = " ", max_len: int = 100) -> str:
    # Normalize (so accents become plain letters when possible)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    # Replace forbidden characters (Windows + Unix)
    s = re.sub(r'[<>:"/\\|?*\x00-\x1F]', replacement, s)
    # Collapse whitespace and separators
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[ .]+$", "", s)  # no trailing dot/space (Windows)
    s = re.sub(rf"{re.escape(replacement)}+", replacement, s)
    # Default name if empty
    if not s:
        s = "untitled"
    # Avoid reserved device names (Windows)
    base = s
    if base.upper() in WINDOWS_RESERVED:
        s = f"_{base}"
    # Enforce length (typical FS limit is 255 bytes; keep margin)
    s = s[:max_len].strip()
    return s
