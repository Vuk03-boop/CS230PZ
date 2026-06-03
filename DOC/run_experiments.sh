#!/usr/bin/env bash
set -euo pipefail

# Skripta stoji u DOC/, ali sve mora da se pokrece iz korena projekta da bi
# uvoz paketa N1 i N2 radio. Zato se prvo prelazi u koren i postavlja
# PYTHONPATH, pa skripta radi bez obzira odakle je pozvana.
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

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
export PYTHONPATH="$ROOT"

# WSL ne prosledjuje promenljive okruzenja Windows procesima, pa Windows python
# ne vidi PYTHONPATH i uvoz paketa N1 puca. WSLENV nabraja koje promenljive da
# se proslede, a zastavica /p uz to prevodi "/mnt/c/..." u "C:\...". Git bash
# prevodi sam i nema wslpath, pa se ova grana tamo preskace.
if [[ "$PY" == *.exe ]] && command -v wslpath >/dev/null 2>&1; then
  export WSLENV="PYTHONPATH/p${WSLENV:+:$WSLENV}"
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

# Ceka da server otvori port pre nego sto se pokrenu radnici. Provera se radi
# istim interpreterom kojim se pokrece i server, a ne preko /dev/tcp: pod WSL-om
# je server Windows proces, a WSL ne vidi njegov 127.0.0.1, pa bi provera iz
# bash-a uvek javljala da servera nema. Ovako proba dolazi iz istog mreznog
# prostora kao i radnici.
wait_for_port() {
  if ! "$PY" - "$PORT" <<'PROBE'
import socket, sys, time
port = int(sys.argv[1])
for _ in range(100):
    try:
        socket.create_connection(("127.0.0.1", port), 0.2).close()
        sys.exit(0)
    except OSError:
        time.sleep(0.1)
sys.exit(1)
PROBE
  then
    echo "server never came up" >&2; exit 1
  fi
}

# Pokrece jedan eksperiment: server i zadati broj radnika, pa ceka da se zavrse.
#
#   run <labela> <n> [zastavice servera] -- [za sve radnike] -- [samo za radnika 1]
#
# Treca grupa postoji zbog ubrizgavanja kvara: kvar sme da pogodi samo jedan
# cvor. Ako istu zastavicu dobiju svi radnici, ceo klaster padne u istoj rundi,
# server zavrsi obuku i figura 4 nema sta da pokaze, jer se ne vidi da obuka
# prezivljava gubitak cvora.
run() {
  local label=$1 n=$2; shift 2
  local sflags=() wflags=() w1flags=()
  while [[ $# -gt 0 && $1 != "--" ]]; do sflags+=("$1"); shift; done
  shift || true
  while [[ $# -gt 0 && $1 != "--" ]]; do wflags+=("$1"); shift; done
  shift || true
  w1flags=("$@")

  echo "=== $label ==="
  "$PY" N1/server.py --workers "$n" --port "$PORT" --db "$DB" --label "$label" \
        --epochs "$EPOCHS" "${sflags[@]}" &
  local srv=$!
  wait_for_port
  "$PY" N1/worker.py --id 1 --port "$PORT" --compute-delay "$DELAY" \
         "${wflags[@]}" "${w1flags[@]}" &
  for i in $(seq 2 "$n"); do
    "$PY" N1/worker.py --id "$i" --port "$PORT" \
           --compute-delay "$DELAY" "${wflags[@]}" &
  done
  wait $srv || true

  # Zamrznut radnik po definiciji nikad ne izlazi sam. Golo "wait" bi zato ovde
  # blokiralo zauvek i zaustavilo sve eksperimente posle ovog, pa se ostaci gase
  # eksplicitno.
  sleep 1
  kill $(jobs -pr) 2>/dev/null || true
  wait 2>/dev/null || true
  sleep 1
}

echo "=== baseline_b128 ==="
"$PY" DOC/baseline.py --batch-size 128 --epochs "$EPOCHS" --compute-delay "$DELAY" \
       --db "$DB" --label baseline_b128

run sync_n1  1 --mode sync
run sync_n2  2 --mode sync
run sync_n4  4 --mode sync
run async_n4 4 --mode async

run crash_n4  4 --mode sync -- -- --crash-at-round 150
run freeze_n4 4 --mode sync --timeout 5 -- -- --freeze-at-round 150

run compress_n4 4 --mode sync -- --compress
run zlib_n4     4 --mode sync --zlib 6 -- --compress --zlib 6

echo "=== static_n4 (straggler, no balancing) ==="
"$PY" N1/server.py --workers 4 --mode sync --port "$PORT" --db "$DB" \
       --label static_n4 --balance static \
       --batch-size 32 --epochs "$EPOCHS" &
srv=$!; wait_for_port
"$PY" N1/worker.py --id 1 --port "$PORT" --delay-per-sample "$SLOW" &
for i in 2 3 4; do
  "$PY" N1/worker.py --id "$i" --port "$PORT" --delay-per-sample "$FAST" &
done
wait $srv || true; wait 2>/dev/null || true; sleep 1

echo "=== balanced_n4 (same straggler, dynamic balancing) ==="
"$PY" N1/server.py --workers 4 --mode sync --port "$PORT" --db "$DB" \
       --label balanced_n4 \
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
       --label checkpoint_a --epochs 3 \
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
       --label checkpoint_b --epochs 3 \
       --checkpoint checkpoints/w.npz --checkpoint-every 10 --resume &
srv=$!; wait_for_port
for i in 1 2; do
  "$PY" N1/worker.py --id "$i" --port "$PORT" --compute-delay "$DELAY" &
done
wait $srv || true; wait 2>/dev/null || true

echo
echo "all runs complete. results/*.csv, results/runs.sqlite"
echo "now: python DOC/plot.py"
