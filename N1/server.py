import argparse
import select
import socket
import time

import numpy as np

from N2 import balancer, store, interceptors, checkpoint
from N1 import common, net

p = argparse.ArgumentParser()
p.add_argument("--workers", type=int, default=2)
p.add_argument("--mode", choices=["sync", "async"], default="sync")
p.add_argument("--lr", type=float, default=0.05)
p.add_argument("--timeout", type=float, default=8.0, help="failure-detector timeout (s)")
p.add_argument("--eval-every", type=int, default=5)
p.add_argument("--port", type=int, default=5555)
p.add_argument("--startup-limit", type=float, default=180.0,
               help="give up if the cluster never forms")
p.add_argument("--balance", choices=["static", "dynamic"], default="static",
               help="static: every worker gets an equal share of the global "
                    "batch. dynamic: shares are sized to each worker's "
                    "measured throughput.")
p.add_argument("--batch-size", type=int, default=32,
               help="per-worker batch; the global batch is this times --workers")
p.add_argument("--epochs", type=int, default=10,
               help="sample budget (the server owns termination)")
p.add_argument("--min-batch", type=int, default=4)
p.add_argument("--zlib", type=int, default=0, metavar="LEVEL",
               help="deflate every message (1-9). Workers must match.")
p.add_argument("--latency", type=float, default=0.0,
               help="injected one-way delay per message (s)")
p.add_argument("--db", default="results/runs.sqlite",
               help="SQLite metrics database ('' to disable)")
p.add_argument("--label", default=None, help="run label in the database")
p.add_argument("--checkpoint", default="", help="path for weight checkpoints")
p.add_argument("--checkpoint-every", type=int, default=0, help="rounds")
p.add_argument("--resume", action="store_true", help="start from --checkpoint")
args = p.parse_args()

SYNC = args.mode == "sync"
LABEL = args.label or "run"

CHAIN = interceptors.build(zlib_level=args.zlib, latency=args.latency)

W = common.init_weights()
X_test, Y_test = common.load_test()

rnd = 0
samples_total = 0
if args.resume and args.checkpoint:
    got = checkpoint.load(args.checkpoint)
    if got is not None:
        W, rnd, samples_total = got
        print(f"[*] resumed from {args.checkpoint} at round {rnd}, "
              f"{samples_total} samples")

n_train = len(common.load_train()[0])
BAL = balancer.WorkBalancer(n_train, args.batch_size, args.workers,
                            args.epochs, mode=args.balance,
                            min_batch=args.min_batch)
BAL.samples_done = samples_total
BAL.cursor = samples_total % n_train

lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
lsock.bind(("", args.port))
lsock.listen(args.workers + 4)

DB = store.Store(args.db or None, label=LABEL, config={
    "mode": args.mode, "workers": args.workers, "lr": args.lr,
    "balance": args.balance, "dataset": common.DATASET,
    "timeout": args.timeout, "batch_size": args.batch_size,
    "epochs": args.epochs, "zlib": args.zlib, "latency": args.latency,
    "interceptors": repr(CHAIN)})

conns = {}
socks = {}
active = set()
last_seen = {}
released_at = {}
responded = set()
buf_grads, buf_meta = [], []

started = False
stopping = False
first_push_at = None
boot = time.time()
t0 = boot
t_last_round = boot
last_eval = (float("nan"), float("nan"))
bytes_total = 0

print(f"[*] server up | mode={args.mode} workers={args.workers} "
      f"balance={args.balance} timeout={args.timeout}s "
      f"dataset={common.DATASET} | {CHAIN}")


# Upisuje metrike jedne runde u bazu i po potrebi pravi checkpoint.
def write_row(train_loss, staleness, n_samples, barrier_wait=0.0):
    global rnd, samples_total, t_last_round, last_eval
    rnd += 1
    samples_total += n_samples
    now = time.time()
    if rnd % args.eval_every == 0 or rnd == 1:
        last_eval = common.evaluate(W, X_test, Y_test)
    row = [rnd, samples_total, round(train_loss, 5),
           round(last_eval[0], 5), round(last_eval[1], 5),
           round(now - t0, 4), len(active), bytes_total,
           round(staleness, 3), round(now - t_last_round, 5),
           round(barrier_wait, 5)]
    DB.round_row(row)
    t_last_round = now
    if args.checkpoint and args.checkpoint_every and \
            rnd % args.checkpoint_every == 0:
        checkpoint.save(args.checkpoint, W, rnd, samples_total,
                        meta=f"{args.mode}/{args.balance}")
    if rnd % 50 == 0 or rnd == 1:
        DB.flush()
        print(f"round {rnd:04d} | train {train_loss:.4f} | test_acc {last_eval[1]:.4f} "
              f"| workers {len(active)} | t {now - t0:6.1f}s "
              f"| imbalance {BAL.imbalance():.2f}x")


# Salje poruku jednom radniku; prekinutu vezu tretira kao otkaz cvora.
def send_to(wid, obj):
    try:
        net.send_msg(socks[wid], obj, CHAIN)
        return True
    except OSError:
        drop(socks[wid], "send failed")
        return False


# Uklanja jednu vezu i izbacuje pripadajuceg radnika iz skupa aktivnih.
def drop(sock, reason):
    wid = conns.pop(sock, None)
    try:
        sock.close()
    except OSError:
        pass
    if wid is not None:
        socks.pop(wid, None)
        if wid in active:
            active.discard(wid)
            responded.discard(wid)
            released_at.pop(wid, None)
            BAL.forget(wid)
            DB.event(rnd, wid, "evicted", reason)
            print(f"[!] {wid} removed at round {rnd} ({reason}); "
                  f"{len(active)} worker(s) left")
    return wid


# Otpusta barijeru: salje nove tezine svim radnicima i resetuje detektor otkaza.
def broadcast(assignments=None):
    now = time.time()
    for w in list(active):
        payload = {"weights": W, "round": rnd}
        if stopping:
            payload["stop"] = True
        elif assignments is not None:
            payload["assign"] = assignments.get(w)
        if send_to(w, payload):
            last_seen[w] = now
            released_at[w] = now


# Spaja prikupljene gradijente po tezinskom proseku, azurira model i otpusta radnike.
def resolve_barrier():
    global W, first_push_at, stopping
    if not buf_grads:
        return
    ns = np.array([m[2] for m in buf_meta], dtype=np.float64)
    stacked = np.stack(buf_grads).astype(np.float64)
    combined = np.tensordot(ns, stacked, axes=(0, 0)) / ns.sum()
    W -= args.lr * combined

    train_loss = float(np.average([m[0] for m in buf_meta], weights=ns))
    staleness = float(np.average([m[1] for m in buf_meta], weights=ns))
    n_samples = int(ns.sum())
    wait = (time.time() - first_push_at) if first_push_at else 0.0

    write_row(train_loss, staleness, n_samples, wait)
    buf_grads.clear()
    buf_meta.clear()
    responded.clear()
    first_push_at = None

    if BAL.exhausted:
        stopping = True
        print("[*] sample budget spent, telling the workers to stop")
    broadcast(None if stopping else BAL.assign(active))


while True:
    readable, _, _ = select.select([lsock] + list(conns), [], [], 0.5)

    for s in readable:
        if s is lsock:
            c, _addr = lsock.accept()
            c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conns[c] = None
            continue

        try:
            msg, nbytes = net.recv_msg(s, CHAIN)
        except OSError:
            msg, nbytes = None, 0

        if msg is None:
            drop(s, "connection closed")
            continue

        bytes_total += nbytes
        wid = msg.get("worker") or conns.get(s)

        if msg["type"] == "PULL":
            conns[s] = wid
            socks[wid] = s
            active.add(wid)
            last_seen[wid] = released_at[wid] = time.time()
            DB.event(rnd, wid, "registered", f"{len(active)}/{args.workers}")
            print(f"[+] {wid} registered ({len(active)}/{args.workers})")
            just_started = False
            if not started and len(active) == args.workers:
                started = True
                just_started = True
                t0 = t_last_round = time.time()
                print("[*] all workers present, timing starts now")

            if not SYNC:
                send_to(wid, {"weights": W, "round": rnd,
                              "assign": BAL.assign_single(wid)})
            elif just_started:
                broadcast(BAL.assign(active))
            elif started:
                send_to(wid, {"weights": W, "round": rnd,
                              "assign": BAL.assign_single(wid)})

        elif msg["type"] == "PUSH":
            if wid not in active:
                drop(s, "push from evicted worker")
                continue
            now = time.time()
            last_seen[wid] = now
            staleness = rnd - msg["round"]
            if wid in released_at:
                elapsed = now - released_at[wid]
                BAL.record(wid, msg["n"], elapsed)
                DB.worker_row(rnd + 1, wid, msg["n"], round(elapsed, 5),
                              int(staleness))

            if not SYNC:
                W -= args.lr * msg["grads"]
                write_row(msg["loss"], staleness, msg["n"])
                a = BAL.assign_single(wid)
                if a is None:
                    stopping = True
                    send_to(wid, {"weights": W, "round": rnd, "stop": True})
                else:
                    send_to(wid, {"weights": W, "round": rnd, "assign": a})
                    released_at[wid] = time.time()
            elif wid not in responded:
                if first_push_at is None:
                    first_push_at = now
                buf_grads.append(msg["grads"])
                buf_meta.append((msg["loss"], staleness, msg["n"]))
                responded.add(wid)

    if started:
        now = time.time()
        dead = [w for w in active
                if w not in responded and now - last_seen[w] > args.timeout]
        for d in dead:
            print(f"[!] timeout after {args.timeout}s: evicting {d}")
            drop(socks[d], "timeout")

    if SYNC and started and active and responded == active:
        resolve_barrier()

    if started and not active:
        break
    if not started and time.time() - boot > args.startup_limit:
        print("[!] cluster never formed, giving up")
        break

if args.checkpoint:
    checkpoint.save(args.checkpoint, W, rnd, samples_total, meta="final")
report = CHAIN.report()
DB.finish(rnd, samples_total, bytes_total, report)
print(f"[*] done: {rnd} rounds, {samples_total} samples, "
      f"{bytes_total / 1e6:.2f} MB received, {time.time() - t0:.1f}s")
print(f"[*] throughput: {BAL.summary()} | imbalance {BAL.imbalance():.2f}x")
for name, s in report.items():
    print(f"[*] interceptor {name}: {s}")
lsock.close()
