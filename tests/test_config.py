import json
import math
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path
from unittest.mock import patch

from idat_sync.config import SyncConfig, load_config


class TestSyncConfig(unittest.TestCase):
    def test_defaults_and_url_normalization(self):
        cfg = SyncConfig(
            moodle_base_url="https://aulavirtual.idat.edu.pe/",
            sharepoint_academic_site_url="https://idat628.sharepoint.com/sites/MATERIALESTED-ACADEMICOIDAT/",
        )

        self.assertEqual(cfg.semester_start, date(2025, 9, 22))
        self.assertEqual(cfg.request_delay, 30.0)
        self.assertIsInstance(cfg.request_delay, float)
        self.assertEqual(cfg.session_file, Path("./session.json"))
        self.assertEqual(cfg.log_file, Path("./log.err"))
        self.assertEqual(cfg.moodle_base_url, "https://aulavirtual.idat.edu.pe")
        self.assertEqual(
            cfg.sharepoint_academic_site_url,
            "https://idat628.sharepoint.com/sites/MATERIALESTED-ACADEMICOIDAT",
        )
        self.assertEqual(cfg.sharepoint_host, "idat628.sharepoint.com")
        self.assertEqual(cfg.sharepoint_personal_host, "idat628-my.sharepoint.com")
        self.assertEqual(cfg.email_domain, "idat.pe")
        self.assertEqual(cfg.recordings_folder_name, "Grabaciones")
        self.assertEqual(cfg.week_folder_prefix, "Semana")

        with self.assertRaises(FrozenInstanceError):
            cfg.request_delay = 10.0  # type: ignore[misc]

    def test_rejects_malformed_urls(self):
        for bad_url in [
            "",
            "aulavirtual.idat.edu.pe",
            "ftp://aulavirtual.idat.edu.pe",
            "https://",
        ]:
            with self.subTest(bad_url=bad_url):
                with self.assertRaises(ValueError):
                    SyncConfig(moodle_base_url=bad_url)
                with self.assertRaises(ValueError):
                    SyncConfig(sharepoint_academic_site_url=bad_url)

    def test_rejects_malformed_hosts(self):
        for bad_host in [
            "",
            "https://idat628.sharepoint.com",
            "idat628.sharepoint.com/sites/foo",
            "idat628.sharepoint.com:8443",
            "user@idat.pe",
        ]:
            with self.subTest(bad_host=bad_host):
                with self.assertRaises(ValueError):
                    SyncConfig(sharepoint_host=bad_host)
                with self.assertRaises(ValueError):
                    SyncConfig(sharepoint_personal_host=bad_host)
                with self.assertRaises(ValueError):
                    SyncConfig(email_domain=bad_host)

    def test_rejects_invalid_folder_names(self):
        for bad_folder in ["", "   ", ".", "..", "a/b", "a\\b", "/abs"]:
            with self.subTest(bad_folder=bad_folder):
                with self.assertRaises(ValueError):
                    SyncConfig(recordings_folder_name=bad_folder)
                with self.assertRaises(ValueError):
                    SyncConfig(week_folder_prefix=bad_folder)

    def test_rejects_invalid_request_delay(self):
        for bad_delay in [-1, -0.01, math.nan, math.inf, -math.inf, True, False]:
            with self.subTest(bad_delay=bad_delay):
                with self.assertRaises(ValueError):
                    SyncConfig(request_delay=bad_delay)  # type: ignore[arg-type]


class TestLoadConfig(unittest.TestCase):
    def test_falls_back_to_defaults_when_default_file_absent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with patch.dict(os.environ, {}, clear=True):
                    cfg = load_config()
            finally:
                os.chdir(old_cwd)

        self.assertEqual(cfg, SyncConfig())

    def test_raises_file_not_found_for_missing_explicit_path_or_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "missing.json"
            with self.assertRaises(FileNotFoundError):
                load_config(missing)

            with patch.dict(os.environ, {"IDAT_SYNC_CONFIG": str(missing)}, clear=True):
                with self.assertRaises(FileNotFoundError):
                    load_config()

    def test_precedence_env_over_json_over_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "custom.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "semester_start": "2026-03-16",
                        "request_delay": 5,
                        "session_file": "./custom_session.json",
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "IDAT_SYNC_CONFIG": str(cfg_path),
                "IDAT_SYNC_REQUEST_DELAY": "1.5",
                "IDAT_SYNC_RECORDINGS_FOLDER_NAME": "Videos",
            }
            with patch.dict(os.environ, env, clear=True):
                cfg = load_config()

        self.assertEqual(cfg.semester_start, date(2026, 3, 16))
        self.assertEqual(cfg.request_delay, 1.5)
        self.assertEqual(cfg.recordings_folder_name, "Videos")
        self.assertEqual(cfg.session_file, Path("./custom_session.json"))
        self.assertEqual(cfg.week_folder_prefix, "Semana")

    def test_rejects_unknown_keys_and_invalid_json_types(self):
        bad_payloads = [
            '["not", "an", "object"]',
            '{"unknown_key": 1}',
            '{"request_delay": true}',
            '{"semester_start": "2026/03/16"}',
            '{"semester_start": 20260316}',
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, payload in enumerate(bad_payloads):
                cfg_path = Path(tmpdir) / f"bad_{idx}.json"
                cfg_path.write_text(payload, encoding="utf-8")
                with self.subTest(payload=payload):
                    with patch.dict(os.environ, {}, clear=True):
                        with self.assertRaises(ValueError):
                            load_config(cfg_path)

    def test_rejects_invalid_env_var_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with patch.dict(os.environ, {"IDAT_SYNC_REQUEST_DELAY": "not_a_float"}, clear=True):
                    with self.assertRaises(ValueError):
                        load_config()
                with patch.dict(os.environ, {"IDAT_SYNC_SEMESTER_START": "bad-date"}, clear=True):
                    with self.assertRaises(ValueError):
                        load_config()
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
