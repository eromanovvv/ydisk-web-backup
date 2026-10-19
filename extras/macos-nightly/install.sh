#!/bin/zsh
# Ставит launchd-агент, который запускает nightly.sh каждую ночь.
#   ./install.sh            — в 01:00
#   ./install.sh 3 30       — в 03:30
#   ./install.sh --remove   — удалить агент
set -e
HERE="${0:A:h}"
LABEL="ydisk-web-backup.nightly"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_=$(id -u)

if [[ "$1" == "--remove" ]]; then
  launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"; echo "Агент удалён."; exit 0
fi
HOUR="${1:-1}"; MIN="${2:-0}"
case "$HERE/" in
  "$HOME/Documents/"*|"$HOME/Desktop/"*|"$HOME/Downloads/"*)
    echo "Репозиторий лежит в $HERE."
    echo "macOS не пускает фоновые задачи (launchd) в «Документы», «Рабочий стол» и «Загрузки» — агент не сможет прочитать скрипт."
    echo "Перенеси репозиторий в домашнюю папку (например, ~/ydisk-web-backup) вместе с chrome-profile/ и nightly.conf,"
    echo "выполни там ./setup.sh и запусти этот install.sh заново."
    echo "Не выдавай /bin/zsh «Полный доступ к диску»: такой доступ получит любой zsh-скрипт на компьютере."
    exit 1 ;;
esac
[[ -f "$HERE/nightly.conf" ]] || { echo "Сначала создай $HERE/nightly.conf (образец: nightly.conf.example)"; exit 1; }
chmod +x "$HERE/nightly.sh"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array><string>/bin/zsh</string><string>$HERE/nightly.sh</string></array>
    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <key>StandardOutPath</key><string>/tmp/$LABEL.log</string>
    <key>StandardErrorPath</key><string>/tmp/$LABEL.log</string>
    <key>EnvironmentVariables</key>
    <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string><key>HOME</key><string>$HOME</string></dict>
</dict>
</plist>
EOF
launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_" "$PLIST"
echo "Агент установлен: каждый день в $(printf '%02d:%02d' $HOUR $MIN) → $HERE/nightly.sh"
echo "Проверить сейчас:  launchctl kickstart gui/$UID_/$LABEL ; cat /tmp/$LABEL.log"
echo
echo "Убедись, что Mac не спит ночью (настройки энергосбережения или: sudo pmset repeat wakeorpoweron MTWRFSU $(printf '%02d:%02d' $HOUR $((MIN>=2 ? MIN-2 : 0))):00)."
