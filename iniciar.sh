#!/usr/bin/env bash
# Instala dependencias (la primera vez) y arranca la app web.
#   ./iniciar.sh            -> app web (http://localhost:8000)
#   ./iniciar.sh telegram   -> bot de Telegram (necesita TELEGRAM_TOKEN; ver .env.ejemplo)
# Pensado para Termux en Android, pero sirve en Linux/Mac.
set -e
cd "$(dirname "$0")"
if [ -n "$PREFIX" ] && [ -d "$PREFIX/bin" ] && command -v pkg >/dev/null 2>&1; then
  command -v python >/dev/null || pkg install -y python
  python -c "import lxml" 2>/dev/null || pkg install -y python-lxml
  python -c "import PIL" 2>/dev/null || pkg install -y python-pillow
  # el SDK de Claude usa pydantic, que en Termux se compila con Rust
  python -c "import pydantic_core" 2>/dev/null || pkg install -y rust binutils
fi
python -m pip install -q -r requirements.txt
[ -f .env ] && set -a && . ./.env && set +a
if [ "$1" = "telegram" ]; then
  exec python telegram_bot.py
fi
exec python app.py
