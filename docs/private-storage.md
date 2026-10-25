# Public code and private financial records

The public Git repository is for application code, blank templates, and fictional
test data. The application's financial records and household configuration are
stored separately. Public source code does not grant access to those records.

## Current development storage

`DRBUDGET_DATA_DIR` identifies an absolute directory outside this checkout.
In the cloud development environment it is `/workspace/private/drbudgettracker`:

```text
drbudgettracker/
  statements/          Original bank and card documents
  raw_sms/             Original transaction alerts
  config/budget.json   Private household settings; initially blank
  exports/             Generated reports
  finance.sqlite3      Private database; initially an empty settings table
```

The initializer refuses paths inside or containing the checkout, rejects symlink
targets, and creates directories accessible only to the operating-system owner
(`0700`) and initial files readable/writable only by that owner (`0600`). It
preserves existing data on repeated runs. Future importers must maintain those
permissions for new files; manually copied files may need their permissions set.

This development directory is not encrypted by this code and is not an isolated
security boundary against other processes running as the same user, workspace
administrators, or agents with access to the workspace. Keep real financial data
out of shareable development environment snapshots. It is not a production
backup or permanent storage service.

## Application requirements before real-data web access

- Put the ledger and household settings in a database reachable only by the
  application backend. Put original statements and raw SMS in private file or
  object storage. Do not publish the storage directory as website assets.
- Require login and check household membership on every read, update, upload,
  export, and download. A hidden URL is not authorization.
- Keep credentials in the hosting provider's secret settings, never in browser
  code. Store household allocations and recipient addresses as private data.
- Use HTTPS, provider encryption at rest, a backup/restore policy, and a deletion
  policy when selecting production hosting. Those services are not configured
  by this development setup.
- Avoid recording raw transaction data or credentials in application logs.
- If Google Sheets is used, restrict the sheet to the intended household and
  backend integration; do not publish it or enable unrestricted link access.

## Git safeguards and their limits

`.gitignore` excludes common credential files, financial exports, and local data
directories. The local pre-commit hook rejects tracked files matching those
exclusions, including files force-added to Git. GitHub Actions runs the same check
as a secondary signal; it runs after upload, so it cannot prevent disclosure.

These checks inspect filenames, not the meaning of file contents. They cannot
detect bank details pasted into a README, renamed documents, or every secret.
Review the exact staged files and their diff before committing. Do not bypass
the hook for real data. `.gitignore` does not remove anything from Git history.

Setup preserves an existing custom hooks configuration and reports it instead of
overwriting it. In that case, integrate `python3 scripts/check_public_files.py`
into the existing pre-commit workflow.
