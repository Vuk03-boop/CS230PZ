import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    mode        TEXT NOT NULL,
    n_workers   INTEGER NOT NULL,
    lr          REAL,
    balance     TEXT,
    dataset     TEXT,
    timeout_s   REAL,
    interceptors TEXT,
    config      TEXT,
    started_at  REAL,
    finished_at REAL,
    total_rounds   INTEGER,
    total_samples  INTEGER,
    total_bytes    INTEGER
);

CREATE TABLE IF NOT EXISTS rounds (
    run_id        INTEGER NOT NULL REFERENCES runs(run_id),
    round         INTEGER NOT NULL,
    samples       INTEGER,
    train_loss    REAL,
    test_loss     REAL,
    test_acc      REAL,
    wall_clock    REAL,
    active_workers INTEGER,
    bytes_in      INTEGER,
    mean_staleness REAL,
    round_seconds REAL,
    barrier_wait  REAL,
    PRIMARY KEY (run_id, round)
);

CREATE TABLE IF NOT EXISTS events (
    run_id  INTEGER NOT NULL REFERENCES runs(run_id),
    ts      REAL,
    round   INTEGER,
    worker  TEXT,
    kind    TEXT,
    detail  TEXT
);

CREATE TABLE IF NOT EXISTS worker_rounds (
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    round       INTEGER NOT NULL,
    worker      TEXT NOT NULL,
    n_samples   INTEGER,
    seconds     REAL,
    staleness   INTEGER,
    PRIMARY KEY (run_id, round, worker)
);

CREATE INDEX IF NOT EXISTS idx_rounds_run ON rounds(run_id);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_wr_run ON worker_rounds(run_id, worker);
"""


# Upisuje metrike runa u SQLite bazu; svaka metoda ne radi nista ako je baza iskljucena.
class Store:

    # Otvara bazu, pravi tabele i upisuje red za novi run.
    def __init__(self, path=None, label="run", config=None):
        self.enabled = bool(path)
        self.run_id = None
        if not self.enabled:
            return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10.0)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        cfg = dict(config or {})
        cur = self.db.execute(
            "INSERT INTO runs (label, mode, n_workers, lr, balance, dataset,"
            " timeout_s, interceptors, config, started_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (label, cfg.get("mode", ""), int(cfg.get("workers", 0)),
             cfg.get("lr"), cfg.get("balance"), cfg.get("dataset"),
             cfg.get("timeout"), cfg.get("interceptors", ""),
             json.dumps(cfg, default=str), time.time()))
        self.run_id = cur.lastrowid
        self.db.commit()

    # Upisuje metrike jedne runde, ista polja koja idu i u CSV.
    def round_row(self, row):
        if not self.enabled:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO rounds VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.run_id,) + tuple(row))

    # Upisuje metrike jednog radnika u jednoj rundi.
    def worker_row(self, rnd, worker, n_samples, seconds, staleness):
        if not self.enabled:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO worker_rounds VALUES (?,?,?,?,?,?)",
            (self.run_id, rnd, worker, n_samples, seconds, staleness))

    # Upisuje diskretan dogadjaj, na primer registraciju ili izbacivanje radnika.
    def event(self, rnd, worker, kind, detail=""):
        if not self.enabled:
            return
        self.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?)",
                        (self.run_id, time.time(), rnd, worker, kind, detail))
        self.db.commit()

    # Potvrdjuje tekucu transakciju.
    def flush(self):
        if self.enabled:
            self.db.commit()

    # Upisuje zavrsne podatke o runu i zatvara bazu.
    def finish(self, rounds, samples, nbytes, interceptor_report=None):
        if not self.enabled:
            return
        self.db.execute(
            "UPDATE runs SET finished_at=?, total_rounds=?, total_samples=?,"
            " total_bytes=?, interceptors=COALESCE(NULLIF(?,''), interceptors)"
            " WHERE run_id=?",
            (time.time(), rounds, samples, nbytes,
             json.dumps(interceptor_report or {}, default=str), self.run_id))
        self.db.commit()
        self.db.close()


# Otvara bazu samo za citanje.
def connect(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


# Vraca poslednji run sa datom oznakom.
def latest_run(db, label):
    r = db.execute("SELECT * FROM runs WHERE label=? ORDER BY run_id DESC"
                   " LIMIT 1", (label,)).fetchone()
    return dict(r) if r else None


# Vraca sve runde jednog runa kao DataFrame.
def rounds_of(db, run_id):
    import pandas as pd
    return pd.read_sql_query(
        "SELECT * FROM rounds WHERE run_id=? ORDER BY round", db,
        params=(run_id,))


# Vraca runde poslednjeg runa sa datom oznakom kao DataFrame ili None.
def load_run(path, label):
    if not os.path.exists(path):
        return None
    db = connect(path)
    try:
        run = latest_run(db, label)
        if run is None:
            return None
        d = rounds_of(db, run["run_id"])
        return d if len(d) else None
    finally:
        db.close()


# Vraca broj uzoraka i utroseno vreme po radniku i rundi za grafik balansiranja.
def worker_balance(path, label):
    import pandas as pd
    if not os.path.exists(path):
        return None
    db = connect(path)
    try:
        run = latest_run(db, label)
        if run is None:
            return None
        d = pd.read_sql_query(
            "SELECT round, worker, n_samples, seconds FROM worker_rounds"
            " WHERE run_id=? ORDER BY round", db, params=(run["run_id"],))
        return d if len(d) else None
    finally:
        db.close()
