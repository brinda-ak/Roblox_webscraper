#!/bin/bash
cd "$(dirname "$0")" || exit 1
echo "Roblox / Minecraft tag analyzer"
echo "First setup downloads Python libraries and an AI model. Allow a few minutes."
if ! command -v python3 >/dev/null 2>&1; then
  echo "Install Python 3.12 or 3.13 from python.org, then reopen this file."
  read -r -p "Press Enter to close."
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv || exit 1
fi
if ! .venv/bin/python -c 'import sentence_transformers, umap, plotly' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt || exit 1
fi
.venv/bin/python analyze.py
read -r -p "Press Enter to close."
