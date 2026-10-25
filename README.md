# DRBudgetTracker

Household finance software. This public repository contains code, documentation,
blank templates, and fictional test data. Financial records and household settings
belong in private storage outside this checkout.

The first working version includes sign-in, a private household dashboard,
effective-dated category allocations, rollover-aware balances, manual transaction
entry, CSV import with review, and an HTML daily email preview. The recap features
three chosen categories, the full budget, and links to correct individual
transactions. Corrections update the ledger, retain an audit trail, and survive
repeat imports. See the [recap requirements](docs/email-recap.md).

This is a local development prototype, not a deployed financial service. Use
fictional data for evaluation. SMS capture, PDF statement parsing, scheduled email
delivery, partner invitations, password recovery, and production hosting are not
implemented. No emails are sent by this code.

## Development setup

The verified cloud lockfile targets Python 3.12 on Linux x86_64. It pins Flask and
all runtime dependencies with artifact hashes. Git is also required.

In the Codex cloud workspace:

```sh
cd /workspace/inicof
export DRBUDGET_DATA_DIR=/workspace/private/drbudgettracker
sh scripts/setup.sh
.venv/bin/python -m unittest discover -s tests -v
python3 scripts/check_public_files.py
.venv/bin/python -m drbudget --port 5055
```

The development server binds only to the loopback interface, with debugging
disabled. Open the sign-in screen in a local development browser, create a
household, and select **Try fictional demo** to explore the flow. A deliberately
miscategorized fictional supermarket purchase demonstrates category correction.
Each new registration creates a separate household; it does not join another one.

On another compatible machine, set `DRBUDGET_DATA_DIR` to an absolute, private
directory outside this repository before running setup from the repository root.
Environment variables must be exported; `.env` files are not loaded automatically.

Setup creates private directories, an empty SQLite settings database, and a blank
budget configuration. It installs the locked dependencies and activates a local
Git pre-commit check. Repeating setup preserves existing data. Starting the web
app adds its ledger tables and a private signing key without replacing the store.

Application settings are stored in the private database, not in the legacy blank
`config/budget.json` template. No actual household values are shipped as defaults.

## Imports and accounting

CSV columns: `date,merchant,amount,currency,kind,account,source_id`, with optional
`category`. Use household-local `YYYY-MM-DD` dates, positive amounts, and one of
the supported kinds shown in the transaction form. Refunds have kind `refund`.
The account label should identify both institution and account, and `source_id`
must be a stable bank identifier. Reused account/source identifiers are skipped;
identical-looking transactions with different identifiers require review. Do not
invent identifiers from row positions in overlapping exports.

Imports are atomic and new rows always start in review. Original CSVs remain in
private storage. This normalized CSV format is not yet a bank-specific parser.

Category allocations apply from the selected month onward. Rollover carries
positive or negative remaining balances, starting from zero at budget inception;
uncapped months do not contribute a carry. Savings categories represent budget
provisions, not actual bank account balances. Secondary currencies are uncapped
and separate. Transfers, repayments, and separate credit offsets are not spending
or income. Ambiguous cash withdrawals require clarification before confirmation.

## Configuration and production boundary

- `DRBUDGET_DATA_DIR`: required absolute private storage path.
- `DRBUDGET_PUBLIC_URL`: optional trusted HTTPS origin for email-preview correction
  links; setting it also requires secure session cookies. Leave unset locally.
- `DRBUDGET_ALLOW_REGISTRATION`: set `0` to disable creation of new households.

Before deployment, select private hosting/storage with encryption and backups,
review authentication and household authorization, configure a production WSGI
server behind HTTPS, and add account recovery and household invitations. Do not
expose Flask's development server. A public URL alone does not deploy the app or
enable sending. See [the storage design](docs/private-storage.md).
