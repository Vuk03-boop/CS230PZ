import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = dict(os.environ, DATASET="synthetic", PYTHONPATH=HERE)
PY = sys.executable


# Pokrece server i radnike za jedan test scenario i ispisuje kraj njihovog izlaza.
def run_cluster(name, port, server_args, worker_args_list, wait=120):
    print(f"\n{'='*70}\n{name}\n{'='*70}")
    srv = subprocess.Popen(
        [PY, os.path.join(HERE, "N1/server.py"), "--port", str(port)] + server_args,
        cwd=HERE, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True)
    time.sleep(2.5)
    ws = []
    for wa in worker_args_list:
        ws.append(subprocess.Popen(
            [PY, os.path.join(HERE, "N1/worker.py"), "--port", str(port)] + wa,
            cwd=HERE, env=ENV, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True))
    try:
        out, _ = srv.communicate(timeout=wait)
    except subprocess.TimeoutExpired:
        srv.kill()
        out, _ = srv.communicate()
        out += "\n*** SERVER TIMED OUT ***"
    for w in ws:
        try:
            wo, _ = w.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            w.kill()
            wo = "*** WORKER TIMED OUT ***"
        tail = [l for l in wo.strip().splitlines() if l][-2:]
        print("   worker:", " | ".join(tail))
    print("\n".join(out.strip().splitlines()[-6:]))
    return out


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"

    if which in ("all", "sync"):
        run_cluster(
            "sync, equal split, 2 workers, 1 epoch", 5701,
            ["--workers", "2", "--mode", "sync", "--epochs", "1",
             "--out", "results/t_sync.csv",
             "--label", "t_sync", "--db", "results/t.sqlite"],
            [["--id", "1"],
             ["--id", "2"]])

    if which in ("all", "dynamic"):
        run_cluster(
            "sync, DYNAMIC balancing, 3 workers, worker-1 is 4x slower", 5702,
            ["--workers", "3", "--mode", "sync", "--balance", "dynamic",
             "--batch-size", "32", "--epochs", "1",
             "--out", "results/t_bal.csv", "--label", "t_bal",
             "--db", "results/t.sqlite"],
            [["--id", "1", "--delay-per-sample", "0.0004"],
             ["--id", "2", "--delay-per-sample", "0.0001"],
             ["--id", "3", "--delay-per-sample", "0.0001"]])

    if which in ("all", "static_straggler"):
        run_cluster(
            "sync, STATIC split, same straggler (for comparison)", 5703,
            ["--workers", "3", "--mode", "sync", "--balance", "static",
             "--epochs", "1", "--out", "results/t_static.csv",
             "--label", "t_static", "--db", "results/t.sqlite"],
            [["--id", "1", "--delay-per-sample", "0.0004"],
             ["--id", "2", "--delay-per-sample", "0.0001"],
             ["--id", "3", "--delay-per-sample", "0.0001"]])

    if which in ("all", "async"):
        run_cluster(
            "async, 2 workers", 5704,
            ["--workers", "2", "--mode", "async", "--epochs", "1",
             "--out", "results/t_async.csv",
             "--label", "t_async", "--db", "results/t.sqlite"],
            [["--id", "1"],
             ["--id", "2"]])

    if which in ("all", "zlib"):
        run_cluster(
            "sync + float32 + deflate interceptors", 5705,
            ["--workers", "2", "--mode", "sync", "--zlib", "6", "--epochs", "1",
             "--out", "results/t_zlib.csv", "--label", "t_zlib",
             "--db", "results/t.sqlite"],
            [["--id", "1", "--compress", "--zlib", "6"],
             ["--id", "2", "--compress", "--zlib", "6"]])

    if which in ("all", "crash"):
        run_cluster(
            "fault tolerance: worker-2 crashes at round 40", 5706,
            ["--workers", "2", "--mode", "sync", "--epochs", "1",
             "--out", "results/t_crash.csv",
             "--label", "t_crash", "--db", "results/t.sqlite"],
            [["--id", "1"],
             ["--id", "2", "--crash-at-round", "40"]])

    if which in ("all", "freeze"):
        run_cluster(
            "fault tolerance: worker-2 freezes at round 40 (timeout 4s)", 5707,
            ["--workers", "2", "--mode", "sync", "--timeout", "4",
             "--epochs", "1", "--out", "results/t_freeze.csv",
             "--label", "t_freeze", "--db", "results/t.sqlite"],
            [["--id", "1"],
             ["--id", "2", "--freeze-at-round", "40"]])
