import argparse
import time

from N1 import common
from N2 import store

p = argparse.ArgumentParser()
p.add_argument("--batch-size", type=int, default=32)
p.add_argument("--epochs", type=int, default=10)
p.add_argument("--lr", type=float, default=0.05)
p.add_argument("--compute-delay", type=float, default=0.0)
p.add_argument("--eval-every", type=int, default=5)
p.add_argument("--db", default="results/runs.sqlite",
               help="SQLite metrics database ('' to disable)")
p.add_argument("--label", default="baseline", help="run label in the database")
args = p.parse_args()

X, Y = common.load_train()
X_test, Y_test = common.load_test()
W = common.init_weights()
print(f"[*] baseline | dataset={common.DATASET} | {len(X)} samples "
      f"| batch {args.batch_size}")

# Sekvencijalni run nema ni servera ni radnika, ali pise u istu bazu i istim
# redosledom kolona kao server, da bi figura 1 mogla da ga crta zajedno sa
# distribuiranim runovima bez posebnog puta za ucitavanje.
DB = store.Store(args.db or None, label=args.label, config={
    "mode": "sequential", "workers": 1, "lr": args.lr,
    "balance": "static", "dataset": common.DATASET,
    "batch_size": args.batch_size, "epochs": args.epochs})

rnd = 0
samples = 0
t0 = time.time()
t_last = t0
ev = (float("nan"), float("nan"))

for epoch in range(args.epochs):
    for i in range(0, len(X), args.batch_size):
        xb, yb = X[i:i + args.batch_size], Y[i:i + args.batch_size]
        if args.compute_delay:
            time.sleep(args.compute_delay)
        grad, loss, acc = common.forward_backward(W, xb, yb)
        W -= args.lr * grad

        rnd += 1
        samples += len(xb)
        now = time.time()
        if rnd % args.eval_every == 0 or rnd == 1:
            ev = common.evaluate(W, X_test, Y_test)
        DB.round_row([rnd, samples, round(loss, 5), round(ev[0], 5), round(ev[1], 5),
                      round(now - t0, 4), 1, 0, 0.0, round(now - t_last, 5), 0.0])
        t_last = now
    DB.flush()
    print(f"epoch {epoch + 1}/{args.epochs} | test_acc {ev[1]:.4f}")

DB.finish(rnd, samples, 0, {})
print(f"[*] done: {rnd} rounds, {samples} samples, {time.time() - t0:.1f}s")
