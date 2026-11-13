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


def _sanitize_segment(
    s: str,
    *,
    replacement: str = " ",
    max_len: int = 100,
    ascii_only: bool = True,
    preserve_extension: bool = False,
) -> str:
    if ascii_only:
        s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    # Replace forbidden characters (Windows + Unix)
    s = re.sub(r'[<>:"/\\|?*\x00-\x1F\x7F]', replacement, s)
    # Collapse whitespace and separators
    s = re.sub(r"\s+", " ", s).strip()
    if replacement:
        s = re.sub(rf"{re.escape(replacement)}+", replacement, s)
    s = re.sub(r"[ .]+$", "", s)  # no trailing dot/space (Windows)

    if preserve_extension and "." in s:
        stem, dot, ext = s.rpartition(".")
        suffix = f"{dot}{ext}"
        if stem and len(suffix) < max_len:
            stem = re.sub(r"[ .]+$", "", stem[: max_len - len(suffix)].strip())
            s = f"{stem}{suffix}" if stem else ""
        else:
            s = re.sub(r"[ .]+$", "", s[:max_len].strip())
    else:
        s = re.sub(r"[ .]+$", "", s[:max_len].strip())

    # Default name if empty or dot traversal
    if not s or s in {".", ".."}:
        s = "untitled"
    # Avoid reserved device names (Windows) on stem before first extension
    stem = s.split(".", 1)[0]
    if stem.upper() in WINDOWS_RESERVED:
        s = f"_{s}"[:max_len] if len(s) >= max_len else f"_{s}"
    return s


def safe_filename(s: str, replacement: str = " ", max_len: int = 100) -> str:
    return _sanitize_segment(
        s, replacement=replacement, max_len=max_len, ascii_only=True
    )


def safe_path_segment(s: str, replacement: str = " ", max_len: int = 100) -> str:
    return _sanitize_segment(
        s,
        replacement=replacement,
        max_len=max_len,
        ascii_only=False,
        preserve_extension=True,
    )
