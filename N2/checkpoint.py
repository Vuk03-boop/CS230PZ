import os
import time

import numpy as np


# Atomicno upisuje tezine i stanje runa na disk i vraca putanju.
def save(path, W, rnd, samples, meta=None):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".{os.path.basename(path)}.{os.getpid()}.tmp.npz")
    np.savez(tmp, W=W, round=np.int64(rnd), samples=np.int64(samples),
             saved_at=np.float64(time.time()),
             meta=np.array(str(meta or ""), dtype=object), allow_pickle=True)
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


# Ucitava checkpoint ili vraca None ako ga nema ili je ostecen.
def load(path):
    if not os.path.exists(path):
        return None
    try:
        d = np.load(path, allow_pickle=True)
        return d["W"], int(d["round"]), int(d["samples"])
    except Exception as e:
        print(f"[!] checkpoint {path} unreadable ({e}); starting from scratch")
        return None
