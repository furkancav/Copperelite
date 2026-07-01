"""SQLite-backed job state so a run of 1500 products is resumable and idempotent."""
from __future__ import annotations
import json
import sqlite3
import time

from .config import DATA_DIR

DB = DATA_DIR / "state.db"


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            url TEXT PRIMARY KEY,
            status TEXT DEFAULT 'pending',   -- pending|done|error|skipped
            listing_id INTEGER,
            data TEXT,                        -- scraped+optimized JSON
            error TEXT,
            updated_at INTEGER
        )""")


def add_urls(urls: list[str]):
    with _conn() as c:
        for u in urls:
            c.execute("INSERT OR IGNORE INTO products(url, updated_at) VALUES(?, ?)",
                      (u, int(time.time())))


def pending(limit: int | None = None) -> list[str]:
    q = "SELECT url FROM products WHERE status IN ('pending','error') ORDER BY rowid"
    if limit:
        q += f" LIMIT {int(limit)}"
    with _conn() as c:
        return [r["url"] for r in c.execute(q)]


def mark_done(url: str, listing_id: int, data: dict):
    with _conn() as c:
        c.execute("UPDATE products SET status='done', listing_id=?, data=?, error=NULL, "
                  "updated_at=? WHERE url=?",
                  (listing_id, json.dumps(data, ensure_ascii=False), int(time.time()), url))


def mark_error(url: str, error: str):
    with _conn() as c:
        c.execute("UPDATE products SET status='error', error=?, updated_at=? WHERE url=?",
                  (error[:1000], int(time.time()), url))


def counts() -> dict:
    with _conn() as c:
        rows = c.execute("SELECT status, COUNT(*) n FROM products GROUP BY status")
        return {r["status"]: r["n"] for r in rows}
