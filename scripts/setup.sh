#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
: "${DRBUDGET_DATA_DIR:?Set DRBUDGET_DATA_DIR to a private absolute path outside the checkout}"
python3 -c 'import sys; assert sys.version_info[:2] == (3, 12), "The cloud lockfile requires Python 3.12"'
python3 -m venv .venv
.venv/bin/python -m pip --cache-dir /tmp/drbudget-pip-cache install --require-hashes -r requirements.lock
.venv/bin/python scripts/init_private_storage.py

configured_hooks=$(git config --get core.hooksPath || true)
if [ -n "$configured_hooks" ] && [ "$configured_hooks" != ".githooks" ]; then
    printf '%s\n' 'Existing custom Git hooks preserved; add the public-file check to your pre-commit workflow.'
elif [ -z "$configured_hooks" ] && [ -x "$(git rev-parse --git-path hooks/pre-commit)" ]; then
    printf '%s\n' 'Existing pre-commit hook preserved; add the public-file check to it.'
else
    chmod +x .githooks/pre-commit
    git config --local core.hooksPath .githooks
    printf '%s\n' 'Public-file pre-commit check enabled.'
fi
python3 scripts/check_public_files.py
