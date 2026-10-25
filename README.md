# DRBudgetTracker

Household finance software. This public repository contains code, documentation,
blank templates, and fictional test data. Financial records and household settings
belong in private storage outside this checkout.

## Development setup

Requires Python 3.11+ and Git. No third-party packages are needed yet.

In the Codex cloud workspace:

```sh
cd /workspace/inicof
export DRBUDGET_DATA_DIR=/workspace/private/drbudgettracker
sh scripts/setup.sh
python3 -m unittest discover -s tests -v
python3 scripts/check_public_files.py
```

On another computer, set `DRBUDGET_DATA_DIR` to an absolute, private directory
outside this repository before running the setup script from the repository root.
The script reads the exported environment variable, not a `.env` file.

Setup creates private directories, an empty SQLite settings database, and a blank
budget configuration. It also activates a local Git pre-commit check. Repeating
setup preserves existing financial records and configuration.

The application interface, authentication, bank imports, and email delivery are
not implemented yet. This is a development storage foundation, not a deployed
financial service. See [the storage design](docs/private-storage.md) before adding
real financial data or deploying an application.
