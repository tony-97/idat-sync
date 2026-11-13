from dataclasses import dataclass, fields
from datetime import date, datetime
import json
import math
import os
from pathlib import Path, PurePath
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def _normalize_url(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"{field_name} must be a valid http/https URL: {value!r}")
    return urlunsplit(parts._replace(path=parts.path.rstrip("/"), query="", fragment=""))


def _normalize_host(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    raw = value.strip()
    if not raw or "@" in raw:
        raise ValueError(f"{field_name} must be a bare hostname: {value!r}")
    parts = urlsplit(f"//{raw}")
    if parts.hostname != raw.lower() or parts.port is not None or parts.path:
        raise ValueError(f"{field_name} must be a bare hostname without scheme, port, or path: {value!r}")
    return parts.hostname


def _normalize_folder_name(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    cleaned = value.strip()
    if (
        not cleaned
        or cleaned in {".", ".."}
        or "/" in cleaned
        or "\\" in cleaned
        or PurePath(cleaned).name != cleaned
    ):
        raise ValueError(f"{field_name} must be a single-segment folder name: {value!r}")
    return cleaned


def _normalize_path(value: str | Path, field_name: str) -> Path:
    if isinstance(value, Path):
        if not str(value):
            raise ValueError(f"{field_name} must not be empty")
        return value
    if isinstance(value, str):
        if not value.strip():
            raise ValueError(f"{field_name} must not be empty")
        return Path(value)
    raise ValueError(f"{field_name} must be a Path or non-empty string")


@dataclass(frozen=True)
class SyncConfig:
    """Validated runtime configuration for course synchronization."""

    semester_start: date = date(2025, 9, 22)
    request_delay: float = 30.0
    session_file: Path = Path("./session.json")
    log_file: Path = Path("./log.err")
    moodle_base_url: str = "https://aulavirtual.idat.edu.pe"
    sharepoint_host: str = "idat628.sharepoint.com"
    sharepoint_personal_host: str = "idat628-my.sharepoint.com"
    sharepoint_academic_site_url: str = (
        "https://idat628.sharepoint.com/sites/MATERIALESTED-ACADEMICOIDAT"
    )
    email_domain: str = "idat.pe"
    recordings_folder_name: str = "Grabaciones"
    week_folder_prefix: str = "Semana"

    def __post_init__(self) -> None:
        if not isinstance(self.semester_start, date) or isinstance(self.semester_start, datetime):
            raise ValueError(f"semester_start must be a date: {self.semester_start!r}")

        if (
            isinstance(self.request_delay, bool)
            or not isinstance(self.request_delay, (int, float))
            or math.isnan(self.request_delay)
            or math.isinf(self.request_delay)
            or self.request_delay < 0
        ):
            raise ValueError(f"request_delay must be a finite non-negative number: {self.request_delay!r}")
        object.__setattr__(self, "request_delay", float(self.request_delay))

        object.__setattr__(self, "session_file", _normalize_path(self.session_file, "session_file"))
        object.__setattr__(self, "log_file", _normalize_path(self.log_file, "log_file"))
        object.__setattr__(
            self, "moodle_base_url", _normalize_url(self.moodle_base_url, "moodle_base_url")
        )
        object.__setattr__(
            self,
            "sharepoint_academic_site_url",
            _normalize_url(self.sharepoint_academic_site_url, "sharepoint_academic_site_url"),
        )
        object.__setattr__(
            self, "sharepoint_host", _normalize_host(self.sharepoint_host, "sharepoint_host")
        )
        object.__setattr__(
            self,
            "sharepoint_personal_host",
            _normalize_host(self.sharepoint_personal_host, "sharepoint_personal_host"),
        )
        object.__setattr__(self, "email_domain", _normalize_host(self.email_domain, "email_domain"))
        object.__setattr__(
            self,
            "recordings_folder_name",
            _normalize_folder_name(self.recordings_folder_name, "recordings_folder_name"),
        )
        object.__setattr__(
            self,
            "week_folder_prefix",
            _normalize_folder_name(self.week_folder_prefix, "week_folder_prefix"),
        )


def _coerce_field(name: str, value: Any) -> Any:
    if name == "semester_start":
        if isinstance(value, str):
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        raise ValueError(f"semester_start must be an ISO YYYY-MM-DD date string: {value!r}")

    if name == "request_delay":
        if isinstance(value, bool):
            raise ValueError(f"request_delay must be numeric, not bool: {value!r}")
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            return float(value.strip())
        raise ValueError(f"request_delay must be numeric: {value!r}")

    return value


def load_config(config_path: str | Path | None = None) -> SyncConfig:
    """Load SyncConfig with precedence: IDAT_SYNC_* env vars > JSON file > defaults."""
    if config_path is not None and (
        not str(config_path).strip()
    ):
        raise ValueError("config_path must not be empty")

    raw_env_config = os.environ.get("IDAT_SYNC_CONFIG")
    env_config_path = raw_env_config.strip() if raw_env_config and raw_env_config.strip() else None
    is_explicit = config_path is not None or env_config_path is not None
    target_path = (
        Path(config_path)
        if config_path is not None
        else Path(env_config_path)
        if env_config_path is not None
        else Path("./config.json")
    )

    raw_json: dict[str, Any] = {}
    if target_path.exists():
        if not target_path.is_file():
            raise ValueError(f"Config path is not a file: {target_path}")
        loaded = json.loads(target_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("Config JSON root must be a JSON object")
        raw_json = loaded
    elif is_explicit:
        raise FileNotFoundError(f"Config file not found: {target_path}")

    valid_fields = {f.name for f in fields(SyncConfig)}
    unknown_keys = sorted(set(raw_json) - valid_fields)
    if unknown_keys:
        raise ValueError(f"Unknown configuration keys: {unknown_keys}")

    overrides: dict[str, Any] = {}
    for field_name in valid_fields:
        env_key = f"IDAT_SYNC_{field_name.upper()}"
        if env_key in os.environ:
            overrides[field_name] = _coerce_field(field_name, os.environ[env_key])
        elif field_name in raw_json:
            overrides[field_name] = _coerce_field(field_name, raw_json[field_name])

    return SyncConfig(**overrides)
