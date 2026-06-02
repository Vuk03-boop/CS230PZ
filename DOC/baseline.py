import argparse
import csv
import os
import time

from N1 import common

p = argparse.ArgumentParser()
p.add_argument("--batch-size", type=int, default=32)
p.add_argument("--epochs", type=int, default=10)
p.add_argument("--lr", type=float, default=0.05)
p.add_argument("--compute-delay", type=float, default=0.0)
p.add_argument("--eval-every", type=int, default=5)
p.add_argument("--out", default="results/baseline.csv")
args = p.parse_args()

X, Y = common.load_train()
X_test, Y_test = common.load_test()
W = common.init_weights()
print(f"[*] baseline | dataset={common.DATASET} | {len(X)} samples "
      f"| batch {args.batch_size}")

os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
f = open(args.out, "w", newline="")
log = csv.writer(f)
log.writerow(["round", "samples", "train_loss", "test_loss", "test_acc",
              "wall_clock", "active_workers", "bytes_in", "mean_staleness",
              "round_seconds", "barrier_wait"])

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
        log.writerow([rnd, samples, round(loss, 5), round(ev[0], 5), round(ev[1], 5),
                      round(now - t0, 4), 1, 0, 0.0, round(now - t_last, 5), 0.0])
        t_last = now
    print(f"epoch {epoch + 1}/{args.epochs} | test_acc {ev[1]:.4f}")

f.close()
print(f"[*] done: {rnd} rounds, {samples} samples, {time.time() - t0:.1f}s")
