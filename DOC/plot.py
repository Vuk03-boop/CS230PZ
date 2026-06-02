import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from N2 import store

R, FIG = "results", "figures"
DB = os.path.join(R, "runs.sqlite")
os.makedirs(FIG, exist_ok=True)


# Ucitava metrike jednog runa iz CSV-a, a ako ga nema onda iz baze.
def load(name):
    path = os.path.join(R, name + ".csv")
    if os.path.exists(path):
        d = pd.read_csv(path)
        if len(d):
            return d
    return store.load_run(DB, name)


# Snima figuru u direktorijum figures kao PNG.
def save(fig, name):
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, name + ".png"), dpi=150)
    plt.close(fig)
    print("wrote", name + ".png")


base, s4 = load("baseline_b128"), load("sync_n4")
if base is not None and s4 is not None:
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(base["samples"], base["test_acc"], label="sequential (batch 128)")
    ax.plot(s4["samples"], s4["test_acc"], "--", label="sync, 4 workers (batch 32 each)")
    ax.set_xlabel("samples processed")
    ax.set_ylabel("test accuracy")
    ax.set_title("Correctness of synchronisation")
    ax.legend()
    ax.grid(alpha=.3)
    save(fig, "fig1_correctness")

runs = {n: load(f"sync_n{n}") for n in (1, 2, 4)}
runs = {n: d for n, d in runs.items() if d is not None}
if runs:
    finals = [d["test_acc"].dropna().iloc[-1] for d in runs.values()]
    TARGET = round(0.9 * min(finals), 3)

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
    times = {}
    for n, d in sorted(runs.items()):
        a1.plot(d["wall_clock"], d["test_acc"], label=f"{n} worker(s)")
        hit = d[d["test_acc"] >= TARGET]
        if len(hit):
            times[n] = hit["wall_clock"].iloc[0]
    a1.axhline(TARGET, color="k", ls=":", alpha=.5)
    a1.set_xlabel("wall-clock time (s)")
    a1.set_ylabel("test accuracy")
    a1.set_title("Convergence in real time")
    a1.legend()
    a1.grid(alpha=.3)

    if times:
        ns = sorted(times)
        t1 = times[ns[0]]
        speedup = [t1 / times[n] for n in ns]
        a2.plot(ns, speedup, "o-", label="measured")
        a2.plot(ns, ns, "k--", alpha=.5, label="ideal (linear)")
        for n, s in zip(ns, speedup):
            a2.annotate(f"{s:.2f}x", (n, s), textcoords="offset points", xytext=(6, -10))
        a2.set_xlabel("number of workers")
        a2.set_ylabel(f"speedup to reach acc {TARGET}")
        a2.set_title("Speedup vs ideal")
        a2.legend()
        a2.grid(alpha=.3)
        print("speedup:", {n: round(t1 / times[n], 2) for n in ns})
    save(fig, "fig2_speedup")

sy, asy = load("sync_n4"), load("async_n4")
if sy is not None and asy is not None:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
    a1.plot(sy["wall_clock"], sy["test_acc"], label="sync")
    a1.plot(asy["wall_clock"], asy["test_acc"], label="async")
    a1.set_xlabel("wall-clock time (s)")
    a1.set_ylabel("test accuracy")
    a1.set_title("Synchronous vs asynchronous")
    a1.legend()
    a1.grid(alpha=.3)

    top = int(asy["mean_staleness"].max())
    a2.hist(asy["mean_staleness"], bins=range(0, max(top, 1) + 2),
            align="left", rwidth=.8)
    a2.set_xlabel("gradient staleness (rounds)")
    a2.set_ylabel("count")
    a2.set_title("Staleness in async mode")
    a2.grid(alpha=.3)
    save(fig, "fig3_sync_vs_async")

cr, fr = load("crash_n4"), load("freeze_n4")
if cr is not None:
    # Vraca broj runde u kojoj je prvi radnik izbacen iz klastera.
    def eviction_round(d):
        drop = d[d["active_workers"] < d["active_workers"].iloc[0]]
        return drop["round"].iloc[0] if len(drop) else None

    ev_c, ev_f = eviction_round(cr), eviction_round(fr) if fr is not None else None

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
    a1.plot(cr["round"], cr["test_acc"], label="process crash")
    if fr is not None:
        a1.plot(fr["round"], fr["test_acc"], label="frozen node")
    if ev_c is not None:
        a1.axvline(ev_c, color="r", ls="--", label=f"worker lost (round {ev_c})")
    a1.set_xlabel("round")
    a1.set_ylabel("test accuracy")
    a1.set_title("Training survives a node failure")
    a1.legend()
    a1.grid(alpha=.3)

    a2.plot(cr["round"], cr["round_seconds"], label="crash: connection closes")
    if fr is not None:
        a2.plot(fr["round"], fr["round_seconds"], label="freeze: timeout expires")
    if ev_c is not None:
        a2.axvline(ev_c, color="r", ls="--", alpha=.6)
    if ev_f is not None:
        a2.axvline(ev_f, color="C1", ls=":", alpha=.6)
    a2.set_yscale("log")
    a2.set_xlabel("round")
    a2.set_ylabel("round duration (s, log)")
    a2.set_title("Cost of detection")
    a2.legend()
    a2.grid(alpha=.3)
    save(fig, "fig4_fault_tolerance")

# Najslabija figura: pokazuje samo da vise radnika salje vise bajtova, sto je
# ocekivano. Figura 7 istu temu obradjuje sa stvarnim rezultatom, pa ova moze
# da se izostavi iz rada ako treba skratiti.
if runs:
    fig, ax = plt.subplots(figsize=(6, 4))
    for n, d in sorted(runs.items()):
        ax.plot(d["samples"], d["bytes_in"] / 1e6, label=f"{n} worker(s)")
    ax.set_xlabel("samples processed")
    ax.set_ylabel("data received by server (MB)")
    ax.set_title("Communication cost")
    ax.legend()
    ax.grid(alpha=.3)
    save(fig, "fig5_bandwidth")

st, dy = load("static_n4"), load("balanced_n4")
wb = store.worker_balance(DB, "balanced_n4")
ws = store.worker_balance(DB, "static_n4")

if wb is not None or (st is not None and dy is not None):
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))

    if wb is not None:
        for w, g in wb.groupby("worker"):
            a1.plot(g["round"], g["n_samples"], label=w, lw=1)
        a1.set_title("Batch size chosen per worker (dynamic)")
    elif ws is not None:
        for w, g in ws.groupby("worker"):
            a1.plot(g["round"], g["n_samples"], label=w, lw=1)
        a1.set_title("Batch size per worker (static)")
    a1.set_xlabel("round")
    a1.set_ylabel("samples assigned")
    a1.legend(fontsize=8)
    a1.grid(alpha=.3)

    for d, lab in ((st, "static split"), (dy, "dynamic balancing")):
        if d is not None and "barrier_wait" in d:
            a2.plot(d["round"], d["barrier_wait"].rolling(20, min_periods=1).mean(),
                    label=lab)
            print(f"mean barrier wait ({lab}): {d['barrier_wait'].mean():.4f}s")
    a2.set_xlabel("round")
    a2.set_ylabel("barrier wait (s, 20-round mean)")
    a2.set_title("Time the fast workers spend idle")
    a2.legend()
    a2.grid(alpha=.3)
    save(fig, "fig6_load_balancing")

plain, f32, zl = load("sync_n4"), load("compress_n4"), load("zlib_n4")
have = [(d, l) for d, l in ((plain, "no interceptor"),
                            (f32, "float32 gradients"),
                            (zl, "float32 + deflate")) if d is not None]
if len(have) > 1:
    fig, ax = plt.subplots(figsize=(6, 4))
    for d, lab in have:
        ax.plot(d["samples"], d["bytes_in"] / 1e6, label=lab)
    ax.set_xlabel("samples processed")
    ax.set_ylabel("data received by server (MB)")
    ax.set_title("Effect of the compression interceptors")
    ax.legend()
    ax.grid(alpha=.3)
    save(fig, "fig7_interceptor_bandwidth")
