"""SQLite persistence. Two tables, no ORM.

`runs` holds a finished report. `cache` memoizes anything expensive and
idempotent (Apify datasets, reel teardowns) keyed by a caller-chosen string, so
re-running a profile costs nothing the second time. That matters more than it
sounds: without it, every iteration on the report prompt re-pays for scraping
and re-downloads every video.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "outlier.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  seed       TEXT NOT NULL,
  created_at TEXT NOT NULL,
  report     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist (
  username   TEXT NOT NULL,
  category   TEXT NOT NULL,
  note       TEXT,
  added_at   TEXT NOT NULL,
  PRIMARY KEY (username, category)
);
CREATE TABLE IF NOT EXISTS snapshots (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  username    TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  followers   INTEGER NOT NULL,
  median_eng  INTEGER NOT NULL,
  eng_rate    REAL NOT NULL,
  post_count  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS breakouts (
  short_code  TEXT PRIMARY KEY,
  username    TEXT NOT NULL,
  category    TEXT,
  detected_at TEXT NOT NULL,
  lift        REAL NOT NULL,
  teardown    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_seed ON runs(seed, created_at DESC);
CREATE INDEX IF NOT EXISTS snapshots_user ON snapshots(username, captured_at DESC);
CREATE INDEX IF NOT EXISTS breakouts_seen ON breakouts(detected_at DESC);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cache_get(key: str):
    with connect() as conn:
        row = conn.execute("SELECT value FROM cache WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def cache_put(key: str, value) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, created_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), _now()),
        )


def cached(key: str, produce):
    """Memoize `produce()` under `key`.

    Exceptions are not cached, so a run that died on a flaky network call
    retries cleanly. Neither are empty results: every producer here returns a
    list of scraped or inferred items, and an empty one always means the lookup
    did not work rather than that the answer is genuinely nothing. Caching those
    made a single bad response permanent and un-retryable.
    """
    hit = cache_get(key)
    if hit:
        return hit
    value = produce()
    if value:
        cache_put(key, value)
    return value


def save_run(seed: str, report: dict) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (seed, created_at, report) VALUES (?, ?, ?)",
            (seed, _now(), json.dumps(report)),
        )
        return int(cur.lastrowid)


def get_run(run_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    return {"id": row["id"], "seed": row["seed"], "createdAt": row["created_at"],
            **json.loads(row["report"])}


def list_runs(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, seed, created_at FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [{"id": r["id"], "seed": r["seed"], "createdAt": r["created_at"]} for r in rows]


# ---------------------------------------------------------------- watchlist


def watch_add(username: str, category: str, note: str = "") -> None:
    """Track an account under a category. The same account can sit in more than
    one category (a good educator is often also a direct competitor), which is
    why the primary key is the pair."""
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO watchlist (username, category, note, added_at) "
            "VALUES (?, ?, ?, ?)",
            (username.lstrip("@"), category, note, _now()),
        )


def watch_remove(username: str, category: str | None = None) -> int:
    with connect() as conn:
        if category:
            cur = conn.execute(
                "DELETE FROM watchlist WHERE username = ? AND category = ?",
                (username.lstrip("@"), category),
            )
        else:
            cur = conn.execute("DELETE FROM watchlist WHERE username = ?", (username.lstrip("@"),))
        return cur.rowcount


def watchlist(category: str | None = None) -> list[dict]:
    sql = "SELECT * FROM watchlist"
    args: tuple = ()
    if category:
        sql += " WHERE category = ?"
        args = (category,)
    sql += " ORDER BY category, username"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def categories() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM watchlist GROUP BY category ORDER BY category"
        ).fetchall()
    return [{"category": r["category"], "accounts": r["n"]} for r in rows]


# ---------------------------------------------------------------- tracking


def add_snapshot(username: str, *, followers: int, median_eng: int,
                 eng_rate: float, post_count: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO snapshots (username, captured_at, followers, median_eng, eng_rate, "
            "post_count) VALUES (?, ?, ?, ?, ?, ?)",
            (username, _now(), followers, median_eng, eng_rate, post_count),
        )


def snapshots(username: str, limit: int = 30) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE username = ? ORDER BY captured_at DESC LIMIT ?",
            (username, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def trend(username: str) -> dict | None:
    """Change between the two most recent snapshots.

    Returns None on the first ever capture: with one data point there is no
    trend, and reporting 0% growth then would be a lie the dashboard repeats.
    """
    rows = snapshots(username, limit=2)
    if len(rows) < 2:
        return None
    now, before = rows[0], rows[1]
    return {
        "since": before["captured_at"],
        "followersDelta": now["followers"] - before["followers"],
        "followersPct": round(
            (now["followers"] - before["followers"]) / before["followers"] * 100, 2
        ) if before["followers"] else 0.0,
        "engRateDelta": round(now["eng_rate"] - before["eng_rate"], 5),
    }


def known_breakouts() -> set[str]:
    """Short codes already torn down, so tracking only pays for what is new."""
    with connect() as conn:
        rows = conn.execute("SELECT short_code FROM breakouts").fetchall()
    return {r["short_code"] for r in rows}


def add_breakout(short_code: str, username: str, category: str, lift: float,
                 teardown: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO breakouts (short_code, username, category, detected_at, "
            "lift, teardown) VALUES (?, ?, ?, ?, ?, ?)",
            (short_code, username, category, _now(), lift, json.dumps(teardown)),
        )


def recent_breakouts(limit: int = 40, category: str | None = None) -> list[dict]:
    sql = "SELECT * FROM breakouts"
    args: tuple = ()
    if category:
        sql += " WHERE category = ?"
        args = (category,)
    sql += " ORDER BY detected_at DESC LIMIT ?"
    args = args + (limit,)
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [
        {"shortCode": r["short_code"], "username": r["username"], "category": r["category"],
         "detectedAt": r["detected_at"], "lift": r["lift"], **json.loads(r["teardown"])}
        for r in rows
    ]
