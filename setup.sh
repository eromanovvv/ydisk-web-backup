#!/bin/sh
# Установка: создаёт виртуальное окружение Python и ставит Playwright.
# Запуск:  ./setup.sh        (macOS / Linux)
# Windows: см. README.md (раздел «Windows»).
set -e
cd "$(dirname "$0")"

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])')
    if [ "$v" -ge 310 ]; then PY="$c"; break; fi
  fi
done
if [ -z "$PY" ]; then
  echo "Нужен Python 3.10 или новее. macOS: brew install python@3.12 ; Ubuntu: sudo apt install python3 python3-venv"; exit 1
fi
echo "Python: $($PY --version)"

if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
echo "Playwright установлен."

# Браузер: если есть Google Chrome или Edge — используем их; иначе ставим встроенный Chromium.
have_browser=0
case "$(uname -s)" in
  Darwin) [ -d "/Applications/Google Chrome.app" ] && have_browser=1; [ -d "/Applications/Microsoft Edge.app" ] && have_browser=1 ;;
  *)      for b in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge; do command -v $b >/dev/null 2>&1 && have_browser=1; done ;;
esac
if [ "$have_browser" = 1 ]; then
  echo "Найден системный Chrome/Edge — будет использован он."
else
  echo "Chrome не найден — ставлю встроенный Chromium (~150 МБ)..."
  .venv/bin/python -m playwright install chromium
fi

mkdir -p chrome-profile
echo
echo "Готово. Запуск:"
echo "  .venv/bin/python ydisk-web-backup.py"
echo "Подробности — README.md"
