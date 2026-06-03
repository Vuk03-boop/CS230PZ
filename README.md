# Distribuirani okvir za mašinsko učenje (CS230, tema 20)

Parameter server + N workers, raw TCP sockets and `select()`, no ZeroMQ and no
ML framework. Softmax regression on MNIST, trained synchronously or
asynchronously across processes, with failure injection, server-side load
balancing, a middleware interceptor chain and a SQLite metrics store.

## Files

| file | role |
|---|---|
| `N1/common.py` | data set (MNIST, or `DATASET=synthetic`), model, gradients |
| `N1/net.py` | length-prefixed framing over TCP; runs messages through the interceptor chain |
| `N2/interceptors.py` | middleware: float32 gradients, deflate, metrics, injected latency |
| `N1/server.py` | parameter server: barrier, failure detection, work allocation, logging |
| `N1/worker.py` | worker node: local gradients, failure injection, server-driven schedule |
| `N2/balancer.py` | throughput estimation and batch-size allocation |
| `N2/checkpoint.py` | atomic weight checkpoints so the server itself is recoverable |
| `N2/store.py` | SQLite schema and read helpers |
| `DOC/baseline.py` | sequential reference run, no network |
| `DOC/plot.py` | every figure |
| `DOC/run_experiments.sh` | every run behind every figure |
| `smoke_test.py` | fast end-to-end check of all modes (`DATASET=synthetic`) |

## Running

Run everything from the project root and put the root on `PYTHONPATH`, so that
`N1` and `N2` are importable. `run_experiments.sh` does this for you; if you
launch a process by hand you have to do it yourself:

```bash
export PYTHONPATH=.                                      # or use: python -m N1.server
python -c "from N1 import common; common.load_train()"   # fetch MNIST once, before anything parallel
bash DOC/run_experiments.sh
python DOC/plot.py
```

Or one configuration by hand:

```bash
export PYTHONPATH=.
python N1/server.py --workers 4 --mode sync --balance dynamic \
       --epochs 10 --label my_run &
for i in 1 2 3 4; do python N1/worker.py --id $i --delay-per-sample 0.0001 & done
```

Without `PYTHONPATH`, `python N1/server.py` fails with
`ModuleNotFoundError: No module named 'N2'`, because Python puts the *script's*
directory on `sys.path`, not the directory you ran it from.

Fast end-to-end check of every mode, no MNIST download needed:

```bash
python smoke_test.py          # all seven scenarios
python smoke_test.py sync     # just one
```

## How the pieces map onto the grading sheet

**N1 — klijent-server (3).** `N1/server.py` + `N1/worker.py`. One listening socket,
`select()` over all connections, explicit message framing in `N1/net.py` because
TCP is a byte stream and `recv(n)` may return fewer than `n` bytes.

**N1 — rad sa fajlovima (2).** `N2/checkpoint.py` writes `W` to disk every K
rounds and `--resume` restores it. The write is
temp-file → `fsync` → `os.replace`, which is atomic within a filesystem, so a
crash mid-write cannot leave a half-readable checkpoint. This closes the hole in
the original design: workers were survivable, the server was not.

**N2 — middleware / interceptori (3).** `N2/interceptors.py`. A chain runs
front-to-back on send and back-to-front on receive, at two levels: object level
(before pickling) and byte level (after). Gradient narrowing to float32 is
object level and one-sided; deflate is byte level and both peers must agree.
Byte accounting and injected WAN latency are interceptors too, which is why
neither the training loop nor the socket code mentions them.

**N2 — rad sa bazom (3).** `N2/store.py`, SQLite, four tables: `runs` (one row per
run with its full configuration), `rounds` (per-round metrics), `events`
(registrations and evictions with reasons), `worker_rounds` (which worker
computed how many samples in how long). Only the server writes, so there is one
writer and no locking problem; WAL is on so `DOC/plot.py` can read during a run.
This is the only place metrics are written — there is no parallel CSV to drift
out of sync with it.

**N2 — load balancer (2).** `N2/balancer.py`, enabled with `--balance dynamic`.
The server keeps an EWMA of each worker's seconds-per-sample and splits a
constant global batch in proportion to measured rate, so a slow node gets a
smaller batch instead of holding up the barrier.

## The one correctness detail worth being ready to defend

Each worker returns a gradient that is a **mean over its own batch**. Averaging
those means with equal weight is only correct when the batches are equal in
size. Under dynamic balancing they are not, so `resolve_barrier()` uses the
weighted combination

    sum(n_i * g_i) / sum(n_i)

which telescopes back to the mean over the union of the batches. Plain
`np.mean` would over-weight the slow workers' smaller batches and quietly
change the objective being optimised. This is also why "N synchronous workers
with batch B behave like one node with batch N·B" still holds with balancing
switched on.

## Measured results (synthetic task, 3 workers, one straggler at 4× per-sample cost)

| | static split | dynamic balancing |
|---|---|---|
| samples per worker per round | 32 / 32 / 32 | 12 / 45 / 39 |
| seconds per worker per round | 0.0140 / 0.0049 / 0.0050 | 0.0067 / 0.0064 / 0.0065 |
| mean barrier wait | 0.0101 s | 0.0010 s |
| mean round duration | 0.0171 s | 0.0098 s |

Compression, same task, 2 workers, one epoch:

| | bytes received by server | wall clock |
|---|---|---|
| none | 19.80 MB | 0.6 s |
| float32 gradients | 9.95 MB | 0.6 s |
| float32 + deflate level 6 | 9.24 MB | 3.1 s |

Deflate on top of float32 bought about 7% more and cost 1.75 s of CPU on a run
that otherwise takes 0.6 s. On loopback the compression interceptor is a clear
loss; it would only pay off once the link is slow enough that 10 MB takes
longer than 1.75 s to send, i.e. below roughly 50 Mbit/s. Report it as a
negative result rather than leaving it out — measuring it is the point.

These numbers are from `DATASET=synthetic`, so the accuracies they come with
are meaningless and must not be presented as MNIST. Re-run on MNIST for the
figures that appear in the paper.

## Reproducing an earlier result

Everything added is off by default: `--balance static`, no interceptors beyond
byte counting, `--db` writes a new run row rather than touching old ones. A run
with no new flags produces the same trajectory as the original code.
