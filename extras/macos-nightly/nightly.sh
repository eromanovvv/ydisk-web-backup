#!/bin/zsh
# Ночной повторный прогон выгрузки для одного или нескольких аккаунтов.
# Список аккаунтов — в nightly.conf рядом с этим файлом (см. nightly.conf.example):
#   <профиль>  <папка назначения>
# Итог пишется в <первая папка назначения>/../ОТЧЁТ-последний-прогон.txt
# Устанавливается как launchd-агент через install.sh; можно запускать и вручную.

HERE="${0:A:h}"
REPO="${HERE:h:h}"
CONF="$HERE/nightly.conf"
[[ -f "$CONF" ]] || { echo "Нет $CONF — скопируй nightly.conf.example и заполни"; exit 1; }
cd "$REPO" || exit 1
caffeinate -i -s -w $$ &    # не давать Mac уснуть, пока идёт прогон
PY="$REPO/.venv/bin/python"
TS=$(date +%Y%m%d)

typeset -a PROFILES DESTS
while read -r prof dest rest; do
  [[ -z "$prof" || "$prof" == \#* ]] && continue
  PROFILES+=("$prof"); DESTS+=("${dest/#\~/$HOME}")
done < "$CONF"
[[ ${#PROFILES} -gt 0 ]] || { echo "nightly.conf пуст"; exit 1; }

BASE="${DESTS[1]:h}"
LOGDIR="$BASE/_nightly-logs"; mkdir -p "$LOGDIR"
REPORT="$BASE/ОТЧЁТ-последний-прогон.txt"

if pgrep -f "ydisk-web-backup.py" >/dev/null; then
  echo "Прогон $(date '+%Y-%m-%d %H:%M'): пропущен — предыдущий ещё работает" >> "$REPORT"; exit 0
fi

count_files() { find "$1" -type f ! -path "*/_backup-state/*" ! -name "*.part" 2>/dev/null | wc -l | tr -d ' '; }
typeset -a BEFORE PIDS
START=$(date '+%Y-%m-%d %H:%M')
for i in {1..${#PROFILES}}; do
  BEFORE+=("$(count_files "${DESTS[$i]}")")
  $PY ydisk-web-backup.py --dest "${DESTS[$i]}" --profile "${PROFILES[$i]}" --workers 10 > "$LOGDIR/${PROFILES[$i]}-$TS.log" 2>&1 &
  PIDS+=($!)
  sleep 5
done
wait $PIDS

{
  echo "НОЧНОЙ ПРОГОН: начат $START, завершён $(date '+%Y-%m-%d %H:%M')"
  echo
  for i in {1..${#PROFILES}}; do
    p="${PROFILES[$i]}"; after=$(count_files "${DESTS[$i]}")
    echo "== $p  (${DESTS[$i]}) =="
    tr '\r' '\n' < "$LOGDIR/$p-$TS.log" | grep -E 'Итого:|Скачано|Время|войди|Traceback|Не дождался|Не удалось запустить' | sed 's/^/  /'
    echo "  файлов на диске: было ${BEFORE[$i]}, стало $after (+$((after-BEFORE[$i])))"
    echo "  лог: $LOGDIR/$p-$TS.log"
    echo
  done
  echo "Если в логе «войди в аккаунт» — сессия Яндекса слетела: запусти прогон вручную и войди в открывшемся окне браузера."
} > "$REPORT"
cp "$REPORT" "$LOGDIR/report-$TS.txt"
