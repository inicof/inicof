"""Reject Git-tracked paths that match the repository's ignore rules."""

import argparse
from pathlib import Path
import subprocess


def blocked_files(repository: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-ci", "--exclude-standard", "-z"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    return [name.decode("utf-8", errors="replace")
            for name in result.stdout.split(b"\0") if name]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path,
                        default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        blocked = blocked_files(args.repository)
    except (OSError, subprocess.CalledProcessError):
        print("Could not check the Git index; refusing to report a pass.")
        return 1
    if blocked:
        print("Commit blocked: excluded files are present in the Git index:")
        for name in blocked:
            print(f"  {name!r}")
        print("Keep private data outside this repository and unstage these files.")
        return 1
    print("Public-file check passed (filenames only; review contents separately).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
