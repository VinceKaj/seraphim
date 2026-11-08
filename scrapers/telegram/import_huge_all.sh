#!/usr/bin/env bash
# Import every channel dump in Huge's telegram_data/ into the record store.
# Idempotent (deterministic ids): re-run any time to pick up new messages.
# Language comes from all_channels.csv. War-relevant channels go first.
# Usage: tmux new-session -d -s tg-import seraphim/scrapers/telegram/import_huge_all.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
H="/home/vince/Huge/potato patato"
JOBS=${JOBS:-6}
FIRST="mod_russia AFUStratCom AG_DPR Army_Russia ASGasparyan A_S_Sukonkin"

python3 - "$H" "$FIRST" <<'EOF' > "${TMPDIR:-/tmp}/tg_import_jobs.txt"
import csv, sys, os
h, first = sys.argv[1], sys.argv[2].split()
codes = {"Russian": "ru", "English": "en", "Spanish": "es", "Ukrainian": "uk", "German": "de",
         "Italian": "it", "Portuguese": "pt", "French": "fr"}
lang = {r["Channel_name"].strip().lstrip("@"): codes.get(r["Language"], "") for r in csv.DictReader(open(f"{h}/all_channels.csv"))}
lang["kpszsu"] = "uk"
chans = sorted(d for d in os.listdir(f"{h}/telegram_data") if os.path.isfile(f"{h}/telegram_data/{d}/messages.jsonl"))
chans = [c for c in first if c in chans] + [c for c in chans if c not in first]
for c in chans:
    print(f"{h}/telegram_data/{c}/messages.jsonl\t{lang.get(c, '')}")
EOF

run_one() {
  f="$1"; l="$2"; ch=$(basename "$(dirname "$f")")
  args=("$f"); [ -n "$l" ] && args+=(--lang "$l"); [ -n "${IMPORT_EXTRA:-}" ] && args+=($IMPORT_EXTRA)
  uv run scrapers/telegram/import_jsonl.py "${args[@]}" 2>"logs/import_$ch.err" | tail -1 | sed "s/^/$ch: /"
}
export -f run_one
mkdir -p logs
tr '\n' '\0' < "${TMPDIR:-/tmp}/tg_import_jobs.txt" | xargs -0 -P "$JOBS" -I{} bash -c 'IFS=$'"'"'\t'"'"' read -r f l <<< "{}"; run_one "$f" "$l"'
echo "ALL DONE $(date -u)"
