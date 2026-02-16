#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Creating virtual environment in ${SCRIPT_DIR}/venv ..."
python3 -m venv "${SCRIPT_DIR}/venv"

echo "Installing dependencies ..."
"${SCRIPT_DIR}/venv/bin/pip" install --upgrade pip
"${SCRIPT_DIR}/venv/bin/pip" install -r "${SCRIPT_DIR}/requirements.txt"

echo ""
echo "Done. Activate with:"
echo "  source ${SCRIPT_DIR}/venv/bin/activate"
echo ""
echo "Then run:"
echo "  python ${SCRIPT_DIR}/extract_rules.py --help"
