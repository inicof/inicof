"""Use fictional data only, in automatically removed temporary directories."""

import json
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import unittest

from scripts.check_public_files import blocked_files
from scripts.init_private_storage import REPOSITORY, initialize


class PrivateStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "private"

    def test_initializes_private_files_and_empty_database(self):
        initialize(self.root)
        for name in ("", "statements", "raw_sms", "config", "exports"):
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), 0o700)
        for name in ("finance.sqlite3", "config/budget.json"):
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), 0o600)
        config = json.loads((self.root / "config/budget.json").read_text())
        self.assertIsNone(config["monthly_budget"])
        self.assertEqual(config["recap_recipients"], [])
        with sqlite3.connect(self.root / "finance.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT * FROM settings").fetchall(), [])

    def test_repeated_setup_preserves_settings_and_records(self):
        initialize(self.root)
        config = self.root / "config/budget.json"
        config.write_text('{"fictional_test_setting": "keep"}')
        record = self.root / "raw_sms/example.txt"
        record.write_text("FICTIONAL TEST ONLY")
        with sqlite3.connect(self.root / "finance.sqlite3") as connection:
            connection.execute("INSERT INTO settings VALUES (?, ?)", ("example", "keep"))
        initialize(self.root)
        self.assertEqual(json.loads(config.read_text()), {"fictional_test_setting": "keep"})
        self.assertEqual(record.read_text(), "FICTIONAL TEST ONLY")
        with sqlite3.connect(self.root / "finance.sqlite3") as connection:
            self.assertEqual(connection.execute("SELECT value FROM settings").fetchone(),
                             ("keep",))

    def test_rejects_checkout_relative_and_ancestor_paths(self):
        for path in (REPOSITORY / "data", REPOSITORY, REPOSITORY.parent, Path("private")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                initialize(path)

    def test_rejects_symlink_storage_and_database(self):
        target = Path(self.temporary.name) / "target"
        target.mkdir()
        self.root.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            initialize(self.root)
        self.root.unlink()
        self.root.mkdir()
        sentinel = target / "sentinel"
        sentinel.write_text("FICTIONAL TEST ONLY")
        (self.root / "finance.sqlite3").symlink_to(sentinel)
        with self.assertRaises(ValueError):
            initialize(self.root)
        self.assertEqual(sentinel.read_text(), "FICTIONAL TEST ONLY")

    def test_rejects_hard_link_database(self):
        self.root.mkdir()
        sentinel = Path(self.temporary.name) / "sentinel"
        sentinel.write_text("FICTIONAL TEST ONLY")
        (self.root / "finance.sqlite3").hardlink_to(sentinel)
        with self.assertRaises(ValueError):
            initialize(self.root)
        self.assertEqual(sentinel.read_text(), "FICTIONAL TEST ONLY")


class PublicFileCheckTests(unittest.TestCase):
    def test_force_added_private_files_are_rejected_and_templates_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            shutil.copyfile(REPOSITORY / ".gitignore", repo / ".gitignore")
            names = [".env", "statement.pdf", "transactions.csv", "finance.sqlite3",
                     "raw_sms/message.txt", "screenshots/example.png", "config/budget.json", "credentials.json"]
            allowed = ["README.md", "config/budget.example.json"]
            for name in names + allowed:
                file = repo / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("FICTIONAL TEST ONLY")
            subprocess.run(["git", "add", "-f", "--", *names, *allowed],
                           cwd=repo, check=True)
            self.assertEqual(set(blocked_files(repo)), set(names))
            result = subprocess.run(
                ["python3", str(REPOSITORY / "scripts/check_public_files.py"),
                 "--repository", str(repo)], capture_output=True, check=False
            )
            self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
