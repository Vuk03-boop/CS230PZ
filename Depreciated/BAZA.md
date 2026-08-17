# Database reference

File: `../results/runs.sqlite`. SQLite 3, WAL journal mode.
Schema: `../N2/store.py`, constant `SCHEMA`.
Writers: `../N1/server.py` (all distributed runs) and `../DOC/baseline.py` (sequential
runs). Workers do not write to the database.
Readers: `../N2/store.py` helpers and `../DOC/plot.py`, opened read-only via
`file:<path>?mode=ro`.

Open from the shell:

```bash
sqlite3 results/runs.sqlite
```

Open from Python:

```python
from N2 import store

db = store.connect("../results/runs.sqlite")
run = store.latest_run(db, "sync_n4")
```

---

## Tables

| Table | One row is | Rows currently |
|---|---|---|
| `runs` | one experiment run | 13 |
| `rounds` | run x round | 15684 |
| `worker_rounds` | run x round x worker | 33248 |
| `events` | one discrete event | 76 |

`rounds`, `worker_rounds` and `events` reference `runs(run_id)`.
`run_id` is `INTEGER PRIMARY KEY AUTOINCREMENT`, so it increases chronologically.

Indexes: `idx_rounds_run` on `rounds(run_id)`, `idx_events_run` on
`events(run_id)`, `idx_wr_run` on `worker_rounds(run_id, worker)`.

Primary keys: `rounds(run_id, round)`, `worker_rounds(run_id, round, worker)`.
`events` has no primary key.

---

## Table `runs`

One row per run. Inserted at server startup, updated by `Store.finish()` at the
end of the run.

| Column | Type | Unit | Written | Meaning |
|---|---|---|---|---|
| `run_id` | INTEGER | - | start | primary key, autoincrement |
| `label` | TEXT | - | start | value of `--label`, e.g. `sync_n4`. Not unique |
| `mode` | TEXT | - | start | `sync`, `async` or `sequential` |
| `n_workers` | INTEGER | count | start | value of `--workers` |
| `lr` | REAL | - | start | learning rate |
| `balance` | TEXT | - | start | `static` or `dynamic` |
| `dataset` | TEXT | - | start | always `mnist` |
| `timeout_s` | REAL | seconds | start | failure-detector timeout |
| `interceptors` | TEXT | - | start and end | see below |
| `config` | TEXT (JSON) | - | start | full config dict |
| `started_at` | REAL | Unix time | start | |
| `finished_at` | REAL | Unix time | end | NULL if the run did not finish |
| `total_rounds` | INTEGER | count | end | NULL if the run did not finish |
| `total_samples` | INTEGER | count | end | NULL if the run did not finish |
| `total_bytes` | INTEGER | bytes | end | received by the server after chain decode |

### Column `config`

JSON object. Keys for a server run:
`mode`, `workers`, `lr`, `balance`, `dataset`, `timeout`, `batch_size`,
`epochs`, `zlib`, `latency`, `interceptors`.

Example (`zlib_n4`):

```json
{"mode": "sync", "workers": 4, "lr": 0.05, "balance": "static",
 "dataset": "mnist", "timeout": 8.0, "batch_size": 32, "epochs": 10,
 "zlib": 6, "latency": 0.0, "interceptors": "Chain([zlib, metrics])"}
```

For a `sequential` run the keys `timeout`, `zlib`, `latency` and `interceptors`
are absent:

```json
{"mode": "sequential", "workers": 1, "lr": 0.05, "balance": "static",
 "dataset": "mnist", "batch_size": 128, "epochs": 10}
```

### Column `interceptors`

The type of the value depends on whether the run finished.

Unfinished run: the `repr` of the chain written at startup, a plain string.

```
Chain([metrics])
```

Finished run: a JSON object written by `finish()`, keyed by interceptor name.

```json
{"zlib":    {"raw_bytes": 197478978, "wire_bytes": 160074323,
             "ratio": 0.8106, "cpu_seconds": 6.486},
 "metrics": {"sent": {"?": 3136}, "recv": {"PULL": 4, "PUSH": 3132},
             "bytes_out": 160074323, "bytes_in": 54661627}}
```

Possible keys and their fields:

| Key | Fields |
|---|---|
| `float32` | `messages`, `bytes_saved` |
| `zlib` | `raw_bytes`, `wire_bytes`, `ratio`, `cpu_seconds` |
| `metrics` | `sent` (dict by message type), `recv` (dict by message type), `bytes_out`, `bytes_in` |
| `latency` | `one_way_seconds`, `delayed_messages` |

For the sequential baseline the value is `{}`.
JSON parsing of this column must handle failure, because unfinished runs hold a
non-JSON string.

### Current contents of `runs`

| run_id | label | mode | n_workers | balance | timeout_s | total_rounds | total_samples | total_bytes |
|---|---|---|---|---|---|---|---|---|
| 1 | baseline_b128 | sequential | 1 | static | NULL | 790 | 100000 | 0 |
| 2 | sync_n1 | sync | 1 | static | 8.0 | 3130 | 100000 | 197368208 |
| 3 | sync_n2 | sync | 2 | static | 8.0 | 1565 | 100000 | 197368006 |
| 4 | sync_n4 | sync | 4 | static | 8.0 | 783 | 100064 | 197493716 |
| 5 | async_n4 | async | 4 | static | 8.0 | 3130 | 100000 | 197368367 |
| 6 | crash_n4 | sync | 4 | static | 8.0 | 794 | 99968 | 159659622 |
| 7 | freeze_n4 | sync | 4 | static | 5.0 | 794 | 99968 | 159659622 |
| 8 | compress_n4 | sync | 4 | static | 8.0 | 783 | 100064 | 99255404 |
| 9 | zlib_n4 | sync | 4 | static | 8.0 | 783 | 100064 | 54674171 |
| 10 | static_n4 | sync | 4 | static | 8.0 | 783 | 100064 | 197493716 |
| 11 | balanced_n4 | sync | 4 | dynamic | 8.0 | 784 | 100079 | 197745944 |
| 12 | checkpoint_a | sync | 2 | static | 8.0 | NULL | NULL | NULL |
| 13 | checkpoint_b | sync | 2 | static | 8.0 | 1565 | 100000 | 193584646 |

`lr` is 0.05 and `dataset` is `mnist` for every run.
`run_id` 12 has `finished_at IS NULL`: the server process was killed mid-run.

---

## Table `rounds`

One row per completed round, written by `write_row()` in `N1/server.py` and by
the loop in `DOC/baseline.py`. In `sync` mode a row is produced when the barrier
resolves; in `async` mode on every `PUSH`.

| Column | Type | Unit | Cumulative | Meaning |
|---|---|---|---|---|
| `round` | INTEGER | count | - | round number, starts at 1 |
| `samples` | INTEGER | count | yes | samples processed since the start of the run |
| `train_loss` | REAL | - | no | batch loss, weighted by `n_samples` across workers |
| `test_loss` | REAL | - | no | loss on the 2000-sample held-out set |
| `test_acc` | REAL | fraction 0-1 | no | accuracy on the held-out set |
| `wall_clock` | REAL | seconds | yes | since all workers were present |
| `active_workers` | INTEGER | count | no | workers alive during that round |
| `bytes_in` | INTEGER | bytes | yes | received by the server |
| `mean_staleness` | REAL | rounds | no | weighted mean gradient staleness |
| `round_seconds` | REAL | seconds | no | duration of that round |
| `barrier_wait` | REAL | seconds | no | first `PUSH` to last `PUSH` in the round |

Rounding applied on write: `train_loss`, `test_loss`, `test_acc` to 5 decimals;
`wall_clock` to 4; `mean_staleness` to 3; `round_seconds`, `barrier_wait` to 5.

### Evaluation cadence

`test_loss` and `test_acc` are recomputed only when
`round % eval_every == 0 or round == 1`. Default `--eval-every` is 5. Between
evaluations the previous value is repeated; NULL is not written.

Example, `run_id` 4:

| round | test_acc | test_loss | train_loss |
|---|---|---|---|
| 4 | 0.26 | 2.25139 | 2.13825 |
| 5 | 0.6095 | 2.06185 | 2.11922 |
| 6 | 0.6095 | 2.06185 | 2.0553 |
| 9 | 0.6095 | 2.06185 | 1.98427 |
| 10 | 0.7185 | 1.861 | 1.9057 |

`train_loss` is computed every round.

### Value ranges in the current database

- `mean_staleness` is 0.0 for every run except `run_id` 5 (`async_n4`), where it
  ranges 0.0 to 7.0.
- `barrier_wait` is 0.0 for `async` and `sequential` runs.
- `bytes_in` is 0 for `run_id` 1 (`sequential`).
- `active_workers` is 1 for `run_id` 1.
- Round numbering: `run_id` 12 covers rounds 1-30, `run_id` 13 covers rounds
  31-1565 (started with `--resume`). All other runs start at round 1.

`samples`, `wall_clock` and `bytes_in` are running totals. To get throughput,
divide the last `samples` by the last `wall_clock`; do not sum the columns.

---

## Table `worker_rounds`

One row per worker per round, written by `DB.worker_row()` in `N1/server.py`
when a `PUSH` arrives. Not written by `DOC/baseline.py`.

| Column | Type | Unit | Meaning |
|---|---|---|---|
| `round` | INTEGER | count | round the contribution belongs to |
| `worker` | TEXT | - | `worker-1`, `worker-2`, ... |
| `n_samples` | INTEGER | count | batch size assigned by the server |
| `seconds` | REAL | seconds | from barrier release to arrival of the `PUSH` |
| `staleness` | INTEGER | rounds | `server_round - message_round` |

`seconds` is measured as `now - released_at[worker]`, so it includes network
transfer and waiting, not only gradient computation. Rounded to 5 decimals.

The value written is `rnd + 1`, i.e. the round the contribution belongs to
rather than the last completed round.

Example, `run_id` 11 (`balanced_n4`), round 100:

| worker | n_samples | seconds | staleness |
|---|---|---|---|
| worker-1 | 10 | 0.00502 | 0 |
| worker-2 | 40 | 0.00505 | 0 |
| worker-3 | 39 | 0.00457 | 0 |
| worker-4 | 39 | 0.00497 | 0 |

Mean over rounds > 20:

| label | mean barrier_wait (s) | mean round_seconds (s) |
|---|---|---|
| static_n4 | 0.00993 | 0.01568 |
| balanced_n4 | 0.00066 | 0.00676 |

---

## Table `events`

Written by `DB.event()` and committed immediately, unlike `rounds` and
`worker_rounds`.

| Column | Type | Meaning |
|---|---|---|
| `run_id` | INTEGER | run the event belongs to |
| `ts` | REAL | Unix time of the event |
| `round` | INTEGER | server round counter at the time of the event |
| `worker` | TEXT | worker id |
| `kind` | TEXT | `registered` or `evicted` |
| `detail` | TEXT | see below |

| `kind` | Trigger | `detail` |
|---|---|---|
| `registered` | worker sends `PULL` | cluster fill, e.g. `3/4` |
| `evicted` | worker removed | `timeout`, `connection closed`, `send failed`, or `push from evicted worker` |

Counts in the current database: 39 `registered`, 37 `evicted`.

`registered` events carry `round = 0`, because registration happens before the
first round completes.

Most `evicted` rows occur at the last round of a run: when the sample budget is
spent, workers close the connection and the server removes them through the same
path it uses for failures. Evictions that occur mid-run:

```sql
SELECT r.label, e.round, e.worker, e.detail
FROM events e JOIN runs r USING (run_id)
WHERE e.kind = 'evicted' AND r.label IN ('crash_n4', 'freeze_n4')
ORDER BY e.round LIMIT 3;
```

| label | round | worker | detail |
|---|---|---|---|
| crash_n4 | 150 | worker-1 | connection closed |
| freeze_n4 | 150 | worker-1 | timeout |
| crash_n4 | 794 | worker-4 | connection closed |

`run_id` 6 and 7 both continue to round 794 after the round-150 eviction.

---

## Not stored in the database

- Model weights: written to `Depreciated/checkpoints/w.npz` by `N2/checkpoint.py`.
- Gradients: exist only in messages and in server memory.
- The MNIST dataset: cached in `mnist.npz` by `N1/common.py`.
- Console output: not persisted.

---

## Columns used by each figure

| Figure | Table | Columns |
|---|---|---|
| 1 correctness | `rounds` | `samples`, `test_acc` |
| 2 speedup | `rounds` | `wall_clock`, `test_acc`, `samples` |
| 3 sync vs async | `rounds` | `wall_clock`, `test_acc`, `mean_staleness` |
| 4 fault tolerance | `rounds` | `round`, `test_acc`, `round_seconds`, `active_workers` |
| 5 bandwidth | `rounds` | `samples`, `bytes_in` |
| 6 load balancing | `worker_rounds`, `rounds` | `n_samples`, `seconds`, `barrier_wait` |
| 7 interceptors | `runs` | `interceptors` (JSON) |

---

## Queries

All runs:

```sql
SELECT run_id, label, mode, n_workers, total_rounds, total_samples,
       ROUND(total_bytes / 1e6, 2) AS mb
FROM runs ORDER BY run_id;
```

Final accuracy per run:

```sql
SELECT r.label, MAX(d.round) AS last_round, d.test_acc
FROM rounds d JOIN runs r USING (run_id)
GROUP BY r.run_id ORDER BY r.run_id;
```

Round coverage of the checkpoint runs:

```sql
SELECT r.label, MIN(d.round) AS first_round, MAX(d.round) AS last_round
FROM rounds d JOIN runs r USING (run_id)
WHERE r.label LIKE 'checkpoint%' GROUP BY r.run_id;
```

Mean barrier wait, static vs dynamic, skipping the first 20 rounds:

```sql
SELECT r.label, ROUND(AVG(d.barrier_wait), 5) AS wait_s
FROM rounds d JOIN runs r USING (run_id)
WHERE r.label IN ('static_n4', 'balanced_n4') AND d.round > 20
GROUP BY r.run_id;
```

Throughput of a run:

```sql
SELECT r.label,
       MAX(d.samples) * 1.0 / MAX(d.wall_clock) AS samples_per_second
FROM rounds d JOIN runs r USING (run_id)
GROUP BY r.run_id;
```

Per-worker totals within one run:

```sql
SELECT worker, COUNT(*) AS rounds, SUM(n_samples) AS samples,
       ROUND(AVG(seconds), 5) AS mean_seconds
FROM worker_rounds WHERE run_id = 11 GROUP BY worker;
```

---

## Access rules

- `label` is not unique. Re-running an experiment inserts a new row instead of
  replacing the old one. `store.latest_run()` selects the highest `run_id` for a
  label. Manual queries must filter by `run_id`.
- Only the server writes, so there is a single writer.
- WAL is enabled (`PRAGMA journal_mode=WAL`), `PRAGMA synchronous=NORMAL`.
  `results/runs.sqlite-wal` and `results/runs.sqlite-shm` appear alongside the
  database and are listed in `.gitignore`.
- `rounds` and `worker_rounds` rows are committed every 50 rounds by `flush()`,
  and once more before every checkpoint write. `events` rows and the `runs` row
  are committed immediately.
- `smoke_test.py` writes to `results/smoke/smoke.sqlite`, a separate file.
