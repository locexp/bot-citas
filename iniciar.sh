#!/usr/bin/env bash
# Instala dependencias (la primera vez) y arranca la app web.
# Pensado para Termux en Android, pero sirve en Linux/Mac.
set -e
cd "$(dirname "$0")"
if [ -n "$PREFIX" ] && [ -d "$PREFIX/bin" ] && command -v pkg >/dev/null 2>&1; then
  command -v python >/dev/null || pkg install -y python
  python -c "import lxml" 2>/dev/null || pkg install -y python-lxml
fi
python -m pip install -q -r requirements.txt
exec python app.py
