"""SQLite store: sessions, plays, playlist pool, downloaded podcasts."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .config import config_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY, planned_at REAL, seed INTEGER, minutes REAL,
  status TEXT NOT NULL DEFAULT 'planned', staged_dir TEXT, manifest TEXT, scrobbled_at REAL);
CREATE TABLE IF NOT EXISTS plays(
  session_id INTEGER, artist TEXT, title TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS pool(
  artist TEXT, title TEXT, source TEXT, score REAL, retired INTEGER DEFAULT 0,
  added_at REAL, PRIMARY KEY(artist, title));
CREATE TABLE IF NOT EXISTS scrobbles(
  ts INTEGER, artist TEXT, title TEXT, PRIMARY KEY(ts, artist, title));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS wish_attempts(
  key TEXT PRIMARY KEY, status TEXT, at REAL);
CREATE TABLE IF NOT EXISTS podcasts(
  uuid TEXT PRIMARY KEY, title TEXT, filename TEXT, downloaded_at REAL);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config_dir() / "history.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def add_session(
    db, manifest: dict, minutes: float, seed: int | None, staged_dir: str | None, at: float | None = None
) -> int:
    cur = db.execute(
        "INSERT INTO sessions(planned_at, seed, minutes, staged_dir, manifest) VALUES(?,?,?,?,?)",
        (at if at is not None else time.time(), seed, minutes, staged_dir, json.dumps(manifest, ensure_ascii=False)),
    )
    db.commit()
    return cur.lastrowid


def pending_session(db):
    return db.execute(
        "SELECT * FROM sessions WHERE status='planned' ORDER BY id DESC LIMIT 1"
    ).fetchone()


def mark_scrobbled(db, session_id: int, plays: list[tuple[str, str, float]]) -> None:
    """Idempotent guard: refuses to scrobble the same session twice."""
    row = db.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
    if row is None or row["status"] == "scrobbled":
        raise ValueError(f"session {session_id} missing or already scrobbled")
    db.executemany(
        "INSERT INTO plays(session_id, artist, title, ts) VALUES(?,?,?,?)",
        [(session_id, a, t, ts) for a, t, ts in plays],
    )
    db.execute("UPDATE sessions SET status='scrobbled', scrobbled_at=? WHERE id=?", (time.time(), session_id))
    db.commit()


def supersede_planned(db) -> None:
    db.execute("UPDATE sessions SET status='discarded' WHERE status='planned'")
    db.commit()


def recent_plays(db, days: int) -> set[tuple[str, str]]:
    cutoff = time.time() - days * 86400
    rows = db.execute("SELECT artist, title FROM plays WHERE ts>=?", (cutoff,)).fetchall()
    return {(r["artist"].casefold(), r["title"].casefold()) for r in rows}


def play_counts(db) -> dict[tuple[str, str], int]:
    rows = db.execute("SELECT artist, title, COUNT(*) n FROM plays GROUP BY artist, title").fetchall()
    return {(r["artist"].casefold(), r["title"].casefold()): r["n"] for r in rows}


def discard_pending(db) -> int | None:
    """Drop the staged-but-unsettled session without scrobbling it. Returns its id."""
    row = pending_session(db)
    if row is None:
        return None
    db.execute("UPDATE sessions SET status='discarded' WHERE id=?", (row["id"],))
    db.commit()
    return row["id"]
