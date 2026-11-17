"""Unit tests for SyncConfig validation and load_config precedence."""

import json
import math
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from idat_sync.config import SyncConfig, load_config


class TestSyncConfig(unittest.TestCase):
    """Tests for SyncConfig defaults, normalization, and validation."""

    def test_defaults_and_url_normalization(self):
        """Verify default values, URL normalization, and immutability."""
        cfg = SyncConfig(
            moodle_base_url="https://aulavirtual.idat.edu.pe/",
            sharepoint_academic_site_url=(
                "https://idat628.sharepoint.com/sites/MATERIALESTED-ACADEMICOIDAT/"
            ),
        )

        assert cfg.semester_start == date(2025, 9, 22)
        assert cfg.request_delay == 30.0
        assert isinstance(cfg.request_delay, float)
        assert cfg.session_file == Path("./session.json")
        assert cfg.log_file == Path("./log.err")
        assert cfg.moodle_base_url == "https://aulavirtual.idat.edu.pe"
        assert (
            cfg.sharepoint_academic_site_url
            == "https://idat628.sharepoint.com/sites/MATERIALESTED-ACADEMICOIDAT"
        )
        assert cfg.sharepoint_host == "idat628.sharepoint.com"
        assert cfg.sharepoint_personal_host == "idat628-my.sharepoint.com"
        assert cfg.email_domain == "idat.pe"
        assert cfg.recordings_folder_name == "Grabaciones"
        assert cfg.week_folder_prefix == "Semana"
        assert cfg.timezone == "America/Lima"

        with pytest.raises(FrozenInstanceError):
            cfg.request_delay = 10.0  # type: ignore[misc]

    def test_rejects_malformed_urls(self):
        """Reject empty, non-HTTP(S), or hostless URLs."""
        for bad_url in [
            "",
            "aulavirtual.idat.edu.pe",
            "ftp://aulavirtual.idat.edu.pe",
            "https://",
        ]:
            with self.subTest(bad_url=bad_url):
                with pytest.raises(ValueError, match="valid http/https URL"):
                    SyncConfig(moodle_base_url=bad_url)
                with pytest.raises(ValueError, match="valid http/https URL"):
                    SyncConfig(sharepoint_academic_site_url=bad_url)

    def test_rejects_malformed_hosts(self):
        """Reject host strings with schemes, paths, ports, or userinfo."""
        for bad_host in [
            "",
            "https://idat628.sharepoint.com",
            "idat628.sharepoint.com/sites/foo",
            "idat628.sharepoint.com:8443",
            "user@idat.pe",
        ]:
            with self.subTest(bad_host=bad_host):
                with pytest.raises(ValueError, match="bare hostname"):
                    SyncConfig(sharepoint_host=bad_host)
                with pytest.raises(ValueError, match="bare hostname"):
                    SyncConfig(sharepoint_personal_host=bad_host)
                with pytest.raises(ValueError, match="bare hostname"):
                    SyncConfig(email_domain=bad_host)

    def test_rejects_invalid_folder_names(self):
        """Reject empty, dot, or multi-segment folder names."""
        for bad_folder in ["", "   ", ".", "..", "a/b", "a\\b", "/abs"]:
            with self.subTest(bad_folder=bad_folder):
                with pytest.raises(ValueError, match="single-segment folder name"):
                    SyncConfig(recordings_folder_name=bad_folder)
                with pytest.raises(ValueError, match="single-segment folder name"):
                    SyncConfig(week_folder_prefix=bad_folder)

    def test_rejects_invalid_request_delay(self):
        """Reject negative, non-finite, or boolean request_delay values."""
        for bad_delay in [-1, -0.01, math.nan, math.inf, -math.inf, True, False]:
            with (
                self.subTest(bad_delay=bad_delay),
                pytest.raises(ValueError, match="request_delay"),
            ):
                SyncConfig(request_delay=bad_delay)  # type: ignore[arg-type]

    def test_rejects_invalid_timezone(self):
        """Reject empty, unknown, or non-string timezones."""
        for bad_tz in ["", "   ", "Mars/Olympus", 123]:
            with (
                self.subTest(bad_tz=bad_tz),
                pytest.raises(ValueError, match="timezone"),
            ):
                SyncConfig(timezone=bad_tz)  # type: ignore[arg-type]


class TestLoadConfig(unittest.TestCase):
    """Tests for load_config discovery and precedence rules."""

    def test_falls_back_to_defaults_when_default_file_absent(self):
        """Return default SyncConfig when no config file or env var exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                with patch.dict(os.environ, {}, clear=True):
                    cfg = load_config()
                    missing_root = Path(tmpdir) / "missing_app_dir"
                    root_cfg = load_config(missing_root)
            finally:
                os.chdir(old_cwd)

        assert cfg == SyncConfig()
        assert root_cfg == SyncConfig(
            session_file=missing_root / "session.json",
            log_file=missing_root / "log.err",
        )

    def test_raises_file_not_found_for_missing_env_config(self):
        """Raise FileNotFoundError when IDAT_SYNC_CONFIG points nowhere."""
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "missing.json"
            with (
                patch.dict(os.environ, {"IDAT_SYNC_CONFIG": str(missing)}, clear=True),
                pytest.raises(FileNotFoundError),
            ):
                load_config()

    def test_precedence_env_over_json_over_defaults(self):
        """Prefer IDAT_SYNC_* env vars over JSON file over defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root_dir = Path(tmpdir)
            (root_dir / "config.json").write_text(
                json.dumps(
                    {
                        "semester_start": "2026-03-16",
                        "request_delay": 5,
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "IDAT_SYNC_REQUEST_DELAY": "1.5",
                "IDAT_SYNC_RECORDINGS_FOLDER_NAME": "Videos",
                "IDAT_SYNC_TIMEZONE": "UTC",
            }
            with patch.dict(os.environ, env, clear=True):
                cfg = load_config(root_dir)

        assert cfg.semester_start == date(2026, 3, 16)
        assert cfg.request_delay == 1.5
        assert cfg.recordings_folder_name == "Videos"
        assert cfg.session_file == root_dir / "session.json"
        assert cfg.log_file == root_dir / "log.err"
        assert cfg.week_folder_prefix == "Semana"
        assert cfg.timezone == "UTC"

    def test_env_config_overrides_root_config_json(self):
        """Prefer IDAT_SYNC_CONFIG file over root config.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root_dir = Path(tmpdir)
            (root_dir / "config.json").write_text(
                json.dumps({"request_delay": 99}),
                encoding="utf-8",
            )
            custom_cfg = root_dir / "custom.json"
            custom_cfg.write_text(
                json.dumps(
                    {"request_delay": 5, "session_file": "./custom_session.json"}
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ, {"IDAT_SYNC_CONFIG": str(custom_cfg)}, clear=True
            ):
                cfg = load_config(root_dir)

        assert cfg.request_delay == 5.0
        assert cfg.session_file == Path("./custom_session.json")
        assert cfg.log_file == root_dir / "log.err"

    def test_rejects_unknown_keys_and_invalid_json_types(self):
        """Reject invalid JSON roots, unknown keys, and bad field types."""
        bad_payloads = [
            '["not", "an", "object"]',
            '{"unknown_key": 1}',
            '{"request_delay": true}',
            '{"semester_start": "2026/03/16"}',
            '{"semester_start": "20260316"}',
            '{"semester_start": "2026-W11-1"}',
            '{"semester_start": "2026-3-5"}',
            '{"semester_start": 20260316}',
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, payload in enumerate(bad_payloads):
                sub_dir = Path(tmpdir) / f"case_{idx}"
                sub_dir.mkdir()
                (sub_dir / "config.json").write_text(payload, encoding="utf-8")
                with (
                    self.subTest(payload=payload),
                    patch.dict(os.environ, {}, clear=True),
                    pytest.raises(ValueError, match=r"."),
                ):
                    load_config(sub_dir)

    def test_rejects_invalid_env_var_values(self):
        """Reject malformed IDAT_SYNC_* environment variable values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                with (
                    patch.dict(
                        os.environ,
                        {"IDAT_SYNC_REQUEST_DELAY": "not_a_float"},
                        clear=True,
                    ),
                    pytest.raises(ValueError, match="could not convert"),
                ):
                    load_config()
                with (
                    patch.dict(
                        os.environ,
                        {"IDAT_SYNC_SEMESTER_START": "bad-date"},
                        clear=True,
                    ),
                    pytest.raises(ValueError, match="YYYY-MM-DD"),
                ):
                    load_config()
            finally:
                os.chdir(old_cwd)

    def test_empty_root_or_existing_file_raises_value_error(self):
        """Reject empty or file roots while treating blank env var as unset."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "not_a_dir.txt"
            file_path.write_text("hi", encoding="utf-8")
            old_cwd = Path.cwd()
            try:
                os.chdir(tmpdir)
                with patch.dict(os.environ, {}, clear=True):
                    with pytest.raises(ValueError, match="must not be empty"):
                        load_config("")
                    with pytest.raises(ValueError, match="must not be empty"):
                        load_config("   ")
                    with pytest.raises(ValueError, match="not a directory"):
                        load_config(file_path)

                with patch.dict(os.environ, {"IDAT_SYNC_CONFIG": ""}, clear=True):
                    assert load_config() == SyncConfig()
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
