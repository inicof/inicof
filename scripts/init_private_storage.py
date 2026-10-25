"""Initialize private development storage without importing financial records."""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat


REPOSITORY = Path(__file__).resolve().parents[1]


def reject_symlinks(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("Private storage must not use symlinks")


def check_regular_file(path: Path) -> None:
    reject_symlinks(path)
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Private files must be regular files without hard links")


def initialize(data_dir: Path) -> Path:
    data_dir = data_dir.expanduser()
    if not data_dir.is_absolute():
        raise ValueError("DRBUDGET_DATA_DIR must be an absolute path")
    reject_symlinks(data_dir)
    root = data_dir.resolve()
    if root.is_relative_to(REPOSITORY) or REPOSITORY.is_relative_to(root):
        raise ValueError("Private storage must be outside and not contain the checkout")

    directories = [root, *(root / name for name in
                          ("statements", "raw_sms", "config", "exports"))]
    budget = root / "config" / "budget.json"
    database = root / "finance.sqlite3"
    # Check every existing destination before modifying anything.
    for directory in directories:
        reject_symlinks(directory)
        if directory.exists() and not directory.is_dir():
            raise ValueError("A private storage directory path is already a file")
    for path in (budget, database, *(Path(str(database) + suffix) for suffix in
                                    ("-journal", "-wal", "-shm"))):
        check_regular_file(path)

    previous_umask = os.umask(0o077)
    try:
        for directory in directories:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
        if not budget.exists():
            template = json.loads(
                (REPOSITORY / "config" / "budget.example.json").read_text()
            )
            with budget.open("x", encoding="utf-8") as output:
                json.dump(template, output, indent=2)
                output.write("\n")
        budget.chmod(0o600)
        database.touch(mode=0o600, exist_ok=True)
        database.chmod(0o600)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS settings "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
    finally:
        os.umask(previous_umask)
    return root


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=os.environ.get("DRBUDGET_DATA_DIR"))
    args = parser.parse_args()
    if not args.data_dir:
        parser.error("Set DRBUDGET_DATA_DIR or supply --data-dir")
    try:
        root = initialize(Path(args.data_dir))
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Private storage setup failed: {error}\n")
    print(f"Private development storage initialized: {root}")


if __name__ == "__main__":
    main()
