# Repository boundaries

This is a public repository. Only application source, documentation, blank
templates, and explicitly fictional test fixtures belong in Git.

- Never put real statements, SMS, transactions, account identifiers, household
  allocations, balances, email recipients, or credentials in tracked files.
  This also applies to commit messages, pull requests, issues, screenshots,
  generated reports, test snapshots, and logs.
- Keep financial data in the location specified by `DRBUDGET_DATA_DIR`, outside
  the checkout. Do not read private contents unless the user's task needs them.
- Use placeholders in public templates. Never turn uploaded financial records
  into repository fixtures or bake household settings into application defaults.
- Do not stage entire directories without checking the exact files. Run
  `python3 scripts/check_public_files.py` after staging and review the staged
  diff. Filename checks cannot identify financial information pasted into code.
- Do not serve the private directory as static files. Authentication and
  per-household authorization are required before any web route accesses real
  data. Keep secrets on the server, outside browser bundles and public logs.
- A cloud development workspace is not permanent production storage. Do not
  assume local permissions provide encryption, backups, or user authentication.
- Cloud tasks are already isolated: use the existing checkout; do not create
  worktrees unless the user explicitly requests one.

Validation: `.venv/bin/python -m unittest discover -s tests -v` and
`python3 scripts/check_public_files.py`. Use only fictional data in tests.
