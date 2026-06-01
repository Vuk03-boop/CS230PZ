import argparse
import os
import socket
import sys
import time

import common
from N2 import interceptors
import net

p = argparse.ArgumentParser()
p.add_argument("--id", type=int, required=True)
p.add_argument("--workers", type=int, default=2)
p.add_argument("--batch-size", type=int, default=32)
p.add_argument("--epochs", type=int, default=10)
p.add_argument("--compute-delay", type=float, default=0.0,
               help="simulated FIXED seconds per batch, whatever its size. Models "
                    "per-round overhead (kernel launch, framework dispatch).")
p.add_argument("--delay-per-sample", type=float, default=0.0,
               help="simulated seconds PER SAMPLE. Models the part of the cost "
                    "that scales with the work assigned, which is the part a "
                    "load balancer can actually move. Use this one for the "
                    "balancing experiment: with a fixed per-batch delay the "
                    "server can shrink a straggler's batch to nothing and the "
                    "straggler still takes just as long.")
p.add_argument("--crash-at-epoch", type=int, default=-1, help="exit hard (connection closes)")
p.add_argument("--freeze-at-epoch", type=int, default=-1, help="hang with the socket open")
p.add_argument("--crash-at-round", type=int, default=-1,
               help="same, counted in rounds - use this in server-driven mode, "
                    "where the worker has no notion of an epoch")
p.add_argument("--freeze-at-round", type=int, default=-1)
p.add_argument("--compress", action="store_true",
               help="send gradients as float32 instead of float64")
p.add_argument("--zlib", type=int, default=0, metavar="LEVEL",
               help="deflate every message (1-9). Must match the server.")
p.add_argument("--latency", type=float, default=0.0,
               help="injected one-way delay per message (s)")
p.add_argument("--trace", action="store_true")
p.add_argument("--host", default="localhost")
p.add_argument("--port", type=int, default=5555)
args = p.parse_args()

wid = f"worker-{args.id}"

CHAIN = interceptors.build(compress_f32=args.compress, zlib_level=args.zlib,
                           latency=args.latency, trace=args.trace, tag=wid)

X_all, Y_all = common.load_train()
X, Y = common.shard(X_all, Y_all, args.id - 1, args.workers)
print(f"[*] {wid}: {len(X)} of {len(X_all)} samples | {CHAIN}")

sock = socket.create_connection((args.host, args.port))
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

rounds_done = 0


# Salje jednu poruku serveru i ceka odgovor sa novim tezinama.
def exchange(msg):
    net.send_msg(sock, msg, CHAIN)
    reply, _ = net.recv_msg(sock, CHAIN)
    return reply


# Namerno rusi ili zamrzava radnika radi testiranja otpornosti na otkaze.
def maybe_fail(epoch=None, at_batch_start=True):
    if at_batch_start and epoch is not None:
        if args.crash_at_epoch == epoch:
            print("[!] crashing intentionally (failure injection)")
            os._exit(1)
        if args.freeze_at_epoch == epoch:
            print("[!] freezing intentionally (connection stays open)")
            while True:
                time.sleep(3600)
    if args.crash_at_round == rounds_done:
        print("[!] crashing intentionally (failure injection)")
        os._exit(1)
    if args.freeze_at_round == rounds_done:
        print("[!] freezing intentionally (connection stays open)")
        while True:
            time.sleep(3600)


# Racuna gradijent nad jednim batch-om, salje ga serveru i vraca odgovor.
def step(W, cur_round, xb, yb):
    global rounds_done
    d = args.compute_delay + args.delay_per_sample * len(xb)
    if d:
        time.sleep(d)
    grad, loss, acc = common.forward_backward(W, xb, yb)
    rounds_done += 1
    return exchange({"type": "PUSH", "worker": wid, "grads": grad,
                     "loss": loss, "n": len(xb), "round": cur_round})


reply = exchange({"type": "PULL", "worker": wid})
if reply is None:
    sys.exit(f"[!] {wid}: server refused the connection")

if "assign" in reply or reply.get("stop"):
    print(f"[*] {wid}: server-driven mode (load balancing on)")
    while True:
        if reply is None:
            sys.exit(f"[!] {wid}: server closed the connection (evicted?), stopping")
        if reply.get("stop"):
            break
        W, cur_round = reply["weights"], reply["round"]
        a = reply.get("assign")
        if a is None:
            break
        maybe_fail()
        s, e = a
        reply = step(W, cur_round, X_all[s:e], Y_all[s:e])
        if rounds_done % 50 == 0:
            print(f"{wid} round {rounds_done}")
else:
    W, cur_round = reply["weights"], reply["round"]
    for epoch in range(args.epochs):
        for i in range(0, len(X), args.batch_size):
            xb, yb = X[i:i + args.batch_size], Y[i:i + args.batch_size]
            maybe_fail(epoch if i == 0 else None)
            reply = step(W, cur_round, xb, yb)
            if reply is None:
                sys.exit(f"[!] {wid}: server closed the connection (evicted?), stopping")
            if reply.get("stop"):
                break
            W, cur_round = reply["weights"], reply["round"]
        else:
            print(f"{wid} epoch {epoch + 1}/{args.epochs}")
            continue
        break

sock.close()
print(f"[*] {wid} finished after {rounds_done} rounds")
for name, s in CHAIN.report().items():
    print(f"[*] {wid} interceptor {name}: {s}")
