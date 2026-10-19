#!/bin/zsh
# Выгрузка Яндекс Диска через rclone (REST API). НЕ РАБОТАЕТ, пока API отвечает 410 Gone.
# Оставлен на случай, если API снова заработает: rclone config create ydisk yandex, затем этот скрипт.
# Безопасно запускать много раз: докачивает только недостающие файлы,
# ничего не удаляет ни локально, ни на Диске (используется copy, не sync).
#
# Использование:
#   ./ydisk-backup.sh /Volumes/ИМЯ_ДИСКА/YandexDisk            # выгрузить всё
#   ./ydisk-backup.sh /Volumes/ИМЯ_ДИСКА/YandexDisk "Фотокамера" # только одну папку Диска
#   ./ydisk-backup.sh /Volumes/ИМЯ_ДИСКА/YandexDisk --verify     # сверить MD5 скачанного с Диском
#
# Требует настроенный remote "ydisk" (rclone config create ydisk yandex).

set -u
REMOTE="ydisk"
DEST="${1:?Укажи папку назначения, например /Volumes/MyDisk/YandexDisk}"
SUBDIR="${2:-}"
LOGDIR="$DEST/_rclone-logs"
TS=$(date +%Y%m%d-%H%M%S)

# Папка назначения должна быть на подключённом томе.
VOL=$(echo "$DEST" | cut -d/ -f1-3)
if [[ "$DEST" == /Volumes/* && ! -d "$VOL" ]]; then
  echo "Внешний диск $VOL не подключён. Подключи и запусти снова."; exit 1
fi
mkdir -p "$DEST" "$LOGDIR"

if [[ "$SUBDIR" == "--verify" ]]; then
  echo "Сверяю MD5 файлов на Диске и локально (ничего не скачивает)..."
  rclone check "$REMOTE:" "$DEST" --one-way \
    --exclude "/_rclone-logs/**" \
    --missing-on-dst "$LOGDIR/missing-$TS.txt" \
    --differ "$LOGDIR/differ-$TS.txt" \
    --error "$LOGDIR/check-errors-$TS.txt" \
    --checkers 8 --tpslimit 8 \
    --log-file "$LOGDIR/check-$TS.log" --log-level INFO
  echo "Нет локально: $(wc -l < "$LOGDIR/missing-$TS.txt" | tr -d ' ') файлов -> $LOGDIR/missing-$TS.txt"
  echo "Отличаются:  $(wc -l < "$LOGDIR/differ-$TS.txt" | tr -d ' ') файлов -> $LOGDIR/differ-$TS.txt"
  exit 0
fi

SRC="$REMOTE:$SUBDIR"
LOCAL="$DEST${SUBDIR:+/$SUBDIR}"
LOG="$LOGDIR/copy-$TS.log"

echo "Источник: $SRC"
echo "Куда:     $LOCAL"
echo "Лог:      $LOG"
echo

rclone copy "$SRC" "$LOCAL" \
  --transfers 4 --checkers 8 \
  --tpslimit 8 \
  --retries 3 --retries-sleep 20s \
  --low-level-retries 10 \
  --timeout 5m --contimeout 1m \
  --exclude "/_rclone-logs/**" \
  --progress --stats 30s --stats-one-line \
  --log-file "$LOG" --log-level INFO
RC=$?

# Список файлов, которые Яндекс не отдал в этот прогон.
FAILED="$LOGDIR/failed-$TS.txt"
grep -E "ERROR" "$LOG" | grep -v "not deleting files" \
  | sed -E 's/^.*ERROR : //' | sort -u > "$FAILED"
N=$(wc -l < "$FAILED" | tr -d ' ')

echo
echo "================================================================"
echo "Код завершения rclone: $RC (0 = всё скачано без ошибок)"
echo "Файлов с ошибками в этот прогон: $N"
[[ "$N" -gt 0 ]] && echo "Список: $FAILED" && echo "Запусти скрипт снова через несколько часов — он докачает только их."
echo "================================================================"
exit $RC
