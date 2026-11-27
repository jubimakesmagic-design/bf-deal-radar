"""SQLite persistence for deduplication, price history tracking, and metadata."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bf_deal_radar.models import ScoredDeal


class DealStorage:
    """Lightweight SQLite store for seen deals and historical prices."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen_deals (
                    canonical_key TEXT PRIMARY KEY,
                    asin TEXT,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    price_usd REAL,
                    score INTEGER NOT NULL,
                    notified INTEGER NOT NULL DEFAULT 0,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    notified_at TEXT
                );

                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    price_usd REAL NOT NULL,
                    retailer TEXT,
                    source_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_price_history_key
                    ON price_history (item_key, recorded_at);

                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feed_cache (
                    source_id TEXT PRIMARY KEY,
                    etag TEXT,
                    last_modified TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
            # Safe migration for existing cached SQLite DBs created before v0.4
            cols = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(seen_deals)").fetchall()
            }
            if "price_krw" not in cols:
                self._conn.execute("ALTER TABLE seen_deals ADD COLUMN price_krw INTEGER")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DealStorage:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_feed_cache(self) -> dict[str, tuple[str | None, str | None]]:
        """Return {source_id: (etag, last_modified)} for conditional HTTP requests."""
        rows = self._conn.execute("SELECT source_id, etag, last_modified FROM feed_cache").fetchall()
        return {str(r["source_id"]): (r["etag"], r["last_modified"]) for r in rows}

    def update_feed_cache(self, source_id: str, etag: str | None, last_modified: str | None) -> None:
        if not etag and not last_modified:
            return
        existing = self._conn.execute(
            "SELECT etag, last_modified FROM feed_cache WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        if existing and existing["etag"] == etag and existing["last_modified"] == last_modified:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO feed_cache (source_id, etag, last_modified, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    etag = excluded.etag,
                    last_modified = excluded.last_modified,
                    updated_at = excluded.updated_at
                """,
                (source_id, etag, last_modified, now_iso),
            )

    def is_empty(self) -> bool:
        row = self._conn.execute("SELECT COUNT(*) AS cnt FROM seen_deals").fetchone()
        return int(row["cnt"]) == 0

    def get_seen(self, canonical_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM seen_deals WHERE canonical_key = ?",
            (canonical_key,),
        ).fetchone()
        return dict(row) if row else None

    def get_previous_low_price(self, item_key: str) -> float | None:
        row = self._conn.execute(
            "SELECT MIN(price_usd) AS min_price FROM price_history WHERE item_key = ?",
            (item_key,),
        ).fetchone()
        if row and row["min_price"] is not None:
            return float(row["min_price"])
        return None

    def should_notify(self, deal: ScoredDeal, price_drop_renotify_ratio: float = 0.90) -> bool:
        """Return True if this deal hasn't been notified yet, or dropped significantly in price."""
        seen = self.get_seen(deal.canonical_key)
        if seen is None:
            return True
        if not seen["notified"]:
            # Previously recorded below threshold, now crossed threshold (e.g., upvotes/comments surged)
            return True
        # Already notified: only re-notify if price dropped >= 10% below previously notified price
        if deal.is_domestic_kr:
            prev_krw = seen.get("price_krw")
            curr_krw = deal.primary.price_krw
            if prev_krw is not None and curr_krw is not None and prev_krw > 0:
                if curr_krw <= prev_krw * price_drop_renotify_ratio:
                    return True
            return False

        prev_price = seen["price_usd"]
        curr_price = deal.primary.price_usd
        if prev_price is not None and curr_price is not None and prev_price > 0:
            if curr_price <= prev_price * price_drop_renotify_ratio:
                return True
        return False

    def _latest_recorded_price(self, item_key: str) -> float | None:
        row = self._conn.execute(
            "SELECT price_usd FROM price_history WHERE item_key = ? ORDER BY id DESC LIMIT 1",
            (item_key,),
        ).fetchone()
        if row and row["price_usd"] is not None:
            return float(row["price_usd"])
        return None

    def record_deal(self, deal: ScoredDeal, notified: bool) -> None:
        """Upsert a deal into seen_deals and record price_history only when the price changes."""
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        p = deal.primary
        existing = self.get_seen(deal.canonical_key)

        needs_seen_write = existing is None
        new_notified = 1 if notified else 0
        new_notified_at = now_iso if notified else None
        new_score = deal.score
        stored_price = p.price_usd
        stored_krw = p.price_krw

        if existing is not None:
            new_notified = 1 if (notified or existing["notified"]) else 0
            new_notified_at = now_iso if notified else existing["notified_at"]
            new_score = max(deal.score, int(existing["score"]))
            stored_price = p.price_usd if notified else (
                p.price_usd if existing["price_usd"] is None else existing["price_usd"]
            )
            stored_krw = p.price_krw if notified else (
                p.price_krw if existing.get("price_krw") is None else existing.get("price_krw")
            )
            last_seen_stale = False
            try:
                prev_seen_dt = datetime.fromisoformat(str(existing["last_seen_at"]))
                last_seen_stale = (now_dt - prev_seen_dt) >= timedelta(hours=6)
            except ValueError:
                last_seen_stale = True

            needs_seen_write = (
                notified
                or new_notified != int(existing["notified"])
                or new_score != int(existing["score"])
                or stored_price != existing["price_usd"]
                or stored_krw != existing.get("price_krw")
                or last_seen_stale
            )

        needs_price_history = False
        if p.price_usd is not None:
            latest_price = self._latest_recorded_price(deal.canonical_key)
            needs_price_history = latest_price is None or abs(latest_price - p.price_usd) >= 0.01

        if not needs_seen_write and not needs_price_history:
            return

        with self._conn:
            if existing is None:
                self._conn.execute(
                    """
                    INSERT INTO seen_deals (
                        canonical_key, asin, title, url, price_usd, price_krw, score,
                        notified, first_seen_at, last_seen_at, notified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        deal.canonical_key,
                        p.asin,
                        p.title,
                        p.url,
                        stored_price,
                        stored_krw,
                        new_score,
                        new_notified,
                        now_iso,
                        now_iso,
                        new_notified_at,
                    ),
                )
            elif needs_seen_write:
                self._conn.execute(
                    """
                    UPDATE seen_deals
                    SET title = ?, url = ?, price_usd = ?, price_krw = ?, score = ?,
                        notified = ?, last_seen_at = ?, notified_at = ?
                    WHERE canonical_key = ?
                    """,
                    (
                        p.title,
                        p.url,
                        stored_price,
                        stored_krw,
                        new_score,
                        new_notified,
                        now_iso,
                        new_notified_at,
                        deal.canonical_key,
                    ),
                )

            if needs_price_history and p.price_usd is not None:
                self._conn.execute(
                    """
                    INSERT INTO price_history (item_key, title, price_usd, retailer, source_id, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        deal.canonical_key,
                        p.title,
                        p.price_usd,
                        p.retailer,
                        p.source_id,
                        now_iso,
                    ),
                )

    def prune_old(self, days: int = 14) -> int:
        """Keep the SQLite DB compact by deleting stale entries older than `days`."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn:
            cur1 = self._conn.execute("DELETE FROM seen_deals WHERE last_seen_at < ?", (cutoff,))
            self._conn.execute("DELETE FROM price_history WHERE recorded_at < ?", (cutoff,))
        deleted = cur1.rowcount
        if deleted > 0:
            self._conn.execute("VACUUM")
        return deleted
