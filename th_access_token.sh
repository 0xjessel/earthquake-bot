#!/bin/bash
# Cron wrapper for th_access_token.py (run DAILY; see README).
# - Appends each run's output to th_access_token.log (so failures are visible later).
# - Records "<exit code>\t<epoch>\t<last line>" to .cron_status.token for cron_watchdog,
#   which alerts after 3 consecutive failures.
# - Prints nothing, so cron's MAILTO stays quiet; exits with the script's exit code.
# Extra args are passed through (e.g. `./th_access_token.sh --force`).

DIR="$(cd "$(dirname "$0")" && pwd)" || exit 1
cd "$DIR" || exit 1

LOG="$DIR/th_access_token.log"
STATUS="$DIR/.cron_status.token"
OUT="$(mktemp)"

"$DIR/venv/bin/python" "$DIR/th_access_token.py" "$@" > "$OUT" 2>&1
rc=$?

{ echo "========== [$(date +"%Y-%m-%d %H:%M:%S")] =========="; cat "$OUT"; } >> "$LOG"

detail="$(grep -av '^[[:space:]]*$' "$OUT" | tail -n 1 | tr '\r\t' '  ' | cut -c1-300 \
  | sed -E 's/(access_token=|Bearer )[A-Za-z0-9._-]{6,}/\1***REDACTED***/g')"
[ -n "$detail" ] || detail="exit $rc"
printf "%s\t%s\t%s\n" "$rc" "$(date +%s)" "$detail" >> "$STATUS"

rm -f "$OUT"
exit "$rc"
