import json
import os
import sqlite3
import tempfile
import unittest

from config import DEFAULT_CONFIG, load_config, save_config


class TestConfigStorage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="mimo_cfg_test_")
        self.data_dir = os.path.join(self._tmp, "data")
        self.legacy_dir = os.path.join(self._tmp, "legacy")
        self._orig_data_dir = os.environ.get("MIMO_TOKEN_MONITOR_DATA_DIR")
        self._orig_legacy_dir = os.environ.get("MIMO_LEGACY_CONFIG_DIR")
        os.environ["MIMO_TOKEN_MONITOR_DATA_DIR"] = self.data_dir
        os.environ["MIMO_LEGACY_CONFIG_DIR"] = self.legacy_dir

    def tearDown(self):
        if self._orig_data_dir is None:
            os.environ.pop("MIMO_TOKEN_MONITOR_DATA_DIR", None)
        else:
            os.environ["MIMO_TOKEN_MONITOR_DATA_DIR"] = self._orig_data_dir

        if self._orig_legacy_dir is None:
            os.environ.pop("MIMO_LEGACY_CONFIG_DIR", None)
        else:
            os.environ["MIMO_LEGACY_CONFIG_DIR"] = self._orig_legacy_dir

    def test_default_when_no_sources(self):
        cfg = load_config()
        self.assertEqual(cfg, DEFAULT_CONFIG)

    def test_save_and_load_sqlite(self):
        cfg = load_config()
        cfg["cookie"] = "placeholder-cookie"
        cfg["opacity"] = 0.71
        cfg["position"] = [12, 34]
        cfg["always_on_top"] = False
        save_config(cfg)

        db_path = os.path.join(self.data_dir, "settings.db")
        self.assertTrue(os.path.exists(db_path))

        loaded = load_config()
        self.assertEqual(loaded["cookie"], "placeholder-cookie")
        self.assertAlmostEqual(loaded["opacity"], 0.71)
        self.assertEqual(loaded["position"], [12, 34])
        self.assertFalse(loaded["always_on_top"])

        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT count(1) FROM settings").fetchone()[0]
        self.assertEqual(rows, len(DEFAULT_CONFIG))

        wal = db_path + "-wal"
        shm = db_path + "-shm"
        self.assertFalse(os.path.exists(wal))
        self.assertFalse(os.path.exists(shm))

    def test_migrate_from_legacy_json_when_db_empty(self):
        os.makedirs(self.legacy_dir, exist_ok=True)
        legacy_cfg = {"cookie": "legacy-placeholder", "opacity": 0.66}
        with open(os.path.join(self.legacy_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(legacy_cfg, f, ensure_ascii=False)

        cfg = load_config()
        self.assertEqual(cfg["cookie"], "legacy-placeholder")
        self.assertAlmostEqual(cfg["opacity"], 0.66)

        db_path = os.path.join(self.data_dir, "settings.db")
        self.assertTrue(os.path.exists(db_path))
        self.assertTrue(os.path.exists(os.path.join(self.legacy_dir, "config.json")))

        cfg2 = load_config()
        self.assertEqual(cfg2["cookie"], "legacy-placeholder")

        with open(os.path.join(self.legacy_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"cookie": "stale-legacy-value"}, f, ensure_ascii=False)
        self.assertEqual(load_config()["cookie"], "legacy-placeholder")

    def test_corrupt_db_value_falls_back_to_legacy(self):
        os.makedirs(self.legacy_dir, exist_ok=True)
        with open(os.path.join(self.legacy_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"cookie": "legacy-after-corruption"}, f, ensure_ascii=False)

        db_path = os.path.join(self.data_dir, "settings.db")
        os.makedirs(self.data_dir, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
            conn.execute(
                "INSERT INTO settings(key, value_json) VALUES (?, ?)",
                ("cookie", "not-json"),
            )

        cfg = load_config()
        self.assertEqual(cfg["cookie"], "legacy-after-corruption")

    def test_legacy_fallback_when_db_dir_unusable(self):
        os.makedirs(self.legacy_dir, exist_ok=True)
        legacy_cfg = {"cookie": "fallback-placeholder", "display_mode": "third_party"}
        with open(os.path.join(self.legacy_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(legacy_cfg, f, ensure_ascii=False)

        os.environ["MIMO_TOKEN_MONITOR_DATA_DIR"] = os.path.join(self._tmp, "no-access", "x")

        cfg = load_config()
        self.assertEqual(cfg["cookie"], "fallback-placeholder")
        self.assertEqual(cfg["display_mode"], "third_party")

    def test_save_fallback_atomic_json_when_db_unusable(self):
        os.makedirs(self.legacy_dir, exist_ok=True)

        bad_db_dir = os.path.join(self._tmp, "blocked-db")
        os.makedirs(bad_db_dir, exist_ok=True)
        bad_db_path = os.path.join(bad_db_dir, "settings.db")
        with open(bad_db_path, "w", encoding="utf-8") as f:
            f.write("not-a-sqlite-db")

        os.environ["MIMO_TOKEN_MONITOR_DATA_DIR"] = bad_db_dir

        cfg = load_config()
        cfg["cookie"] = "fallback-save-placeholder"
        save_config(cfg)

        path = os.path.join(self.legacy_dir, "config.json")
        self.assertTrue(os.path.exists(path))

        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["cookie"], "fallback-save-placeholder")
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_unknown_fields_preserved(self):
        cfg = load_config()
        cfg["cookie"] = "placeholder"
        cfg["unknown_field"] = 123
        save_config(cfg)

        loaded = load_config()
        self.assertEqual(loaded["unknown_field"], 123)


if __name__ == "__main__":
    unittest.main()
