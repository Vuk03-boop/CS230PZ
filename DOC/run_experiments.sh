#!/usr/bin/env bash
set -euo pipefail

# Skripta stoji u DOC/, ali sve mora da se pokrece iz korena projekta da bi
# uvoz paketa N1 i N2 radio. Zato se prvo prelazi u koren i postavlja
# PYTHONPATH, pa skripta radi bez obzira odakle je pozvana.
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
export PYTHONPATH="$ROOT"

# Bira interpreter: prvo iz .venv ako postoji, inace onaj sa PATH-a. Golo
# "python" u bash-u na Windows-u pokazuje na sistemski Python bez numpy-ja.
# Moze da se pregazi preko promenljive: PY=/putanja/do/python bash ...
if [[ -z "${PY:-}" ]]; then
  if [[ -x "$ROOT/.venv/Scripts/python.exe" ]]; then
    PY="$ROOT/.venv/Scripts/python.exe"
  elif [[ -x "$ROOT/.venv/bin/python" ]]; then
    PY="$ROOT/.venv/bin/python"
  else
    PY=python
  fi
fi
echo "[*] interpreter: $PY"

DELAY=${DELAY:-0.004}
SLOW=${SLOW:-0.0004}
FAST=${FAST:-0.0001}
EPOCHS=${EPOCHS:-10}
PORT=${PORT:-5555}
DB=results/runs.sqlite

mkdir -p results figures checkpoints

"$PY" -c "from N1 import common; common.load_train(); common.load_test(); print('[*] dataset ready')"

# Ceka da server otvori port pre nego sto se pokrenu radnici.
wait_for_port() {
  for _ in $(seq 1 50); do
    (echo >/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null && return 0
    sleep 0.1
  done
  echo "server never came up" >&2; exit 1
}

# Pokrece jedan eksperiment: server i zadati broj radnika, pa ceka da se zavrse.
run() {
  local label=$1 n=$2; shift 2
  local sflags=() wflags=()
  while [[ $# -gt 0 && $1 != "--" ]]; do sflags+=("$1"); shift; done
  shift || true
  wflags=("$@")

  echo "=== $label ==="
  "$PY" N1/server.py --workers "$n" --port "$PORT" --db "$DB" --label "$label" \
        --epochs "$EPOCHS" --out "results/$label.csv" "${sflags[@]}" &
  local srv=$!
  wait_for_port
  for i in $(seq 1 "$n"); do
    "$PY" N1/worker.py --id "$i" --port "$PORT" \
           --compute-delay "$DELAY" "${wflags[@]}" &
  done
  wait $srv || true
  wait 2>/dev/null || true
  sleep 1
}

echo "=== baseline_b128 ==="
"$PY" DOC/baseline.py --batch-size 128 --epochs "$EPOCHS" --compute-delay "$DELAY" \
       --out results/baseline_b128.csv

run sync_n1  1 --mode sync
run sync_n2  2 --mode sync
run sync_n4  4 --mode sync
run async_n4 4 --mode async

run crash_n4  4 --mode sync -- --crash-at-round 150
run freeze_n4 4 --mode sync --timeout 5 -- --freeze-at-round 150

run compress_n4 4 --mode sync -- --compress
run zlib_n4     4 --mode sync --zlib 6 -- --compress --zlib 6

echo "=== static_n4 (straggler, no balancing) ==="
"$PY" N1/server.py --workers 4 --mode sync --port "$PORT" --db "$DB" \
       --label static_n4 --out results/static_n4.csv --balance static \
       --batch-size 32 --epochs "$EPOCHS" &
srv=$!; wait_for_port
"$PY" N1/worker.py --id 1 --port "$PORT" --delay-per-sample "$SLOW" &
for i in 2 3 4; do
  "$PY" N1/worker.py --id "$i" --port "$PORT" --delay-per-sample "$FAST" &
done
wait $srv || true; wait 2>/dev/null || true; sleep 1

echo "=== balanced_n4 (same straggler, dynamic balancing) ==="
"$PY" N1/server.py --workers 4 --mode sync --port "$PORT" --db "$DB" \
       --label balanced_n4 --out results/balanced_n4.csv \
       --balance dynamic --batch-size 32 --epochs "$EPOCHS" &
srv=$!; wait_for_port
"$PY" N1/worker.py --id 1 --port "$PORT" --delay-per-sample "$SLOW" &
for i in 2 3 4; do
  "$PY" N1/worker.py --id "$i" --port "$PORT" --delay-per-sample "$FAST" &
done
wait $srv || true; wait 2>/dev/null || true; sleep 1

# Ova dva runa se ne crtaju ni na jednoj figuri, ali su jedini dokaz za stavku
# "Rad sa fajlovima": server se ubija i ponovo pokrece sa --resume. Dokaz je
# poruka "resumed from ... at round N" u ispisu, pa je treba citirati u radu.
echo "=== checkpoint_n2: server dies and resumes ==="
"$PY" N1/server.py --workers 2 --mode sync --port "$PORT" --db "$DB" \
       --label checkpoint_a --out results/checkpoint_a.csv --epochs 3 \
       --checkpoint checkpoints/w.npz --checkpoint-every 10 &
srv=$!; wait_for_port
for i in 1 2; do
  "$PY" N1/worker.py --id "$i" --port "$PORT" --compute-delay "$DELAY" &
done
sleep 6
echo "[!] killing the server"
kill -9 $srv 2>/dev/null || true
wait 2>/dev/null || true
sleep 1
"$PY" N1/server.py --workers 2 --mode sync --port "$PORT" --db "$DB" \
       --label checkpoint_b --out results/checkpoint_b.csv --epochs 3 \
       --checkpoint checkpoints/w.npz --checkpoint-every 10 --resume &
srv=$!; wait_for_port
for i in 1 2; do
  "$PY" N1/worker.py --id "$i" --port "$PORT" --compute-delay "$DELAY" &
done
wait $srv || true; wait 2>/dev/null || true

echo
echo "all runs complete. results/*.csv, results/runs.sqlite"
echo "now: python DOC/plot.py"
