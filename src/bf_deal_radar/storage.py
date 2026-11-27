"""SQLite persistence for deduplication, price history tracking, and metadata."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bf_deal_radar.dedup import make_url_key
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

    @staticmethod
    def _candidate_keys(deal: ScoredDeal) -> list[str]:
        """Return primary canonical_key plus URL keys for all merged sightings of this deal."""
        keys: list[str] = [deal.canonical_key]
        seen = {deal.canonical_key}
        for url in [deal.primary.url, *deal.merged_urls.values()]:
            url_key = make_url_key(url)
            if url_key and url_key not in seen:
                seen.add(url_key)
                keys.append(url_key)
        return keys

    def get_previous_low_price(self, item_key: str) -> float | None:
        row = self._conn.execute(
            "SELECT MIN(price_usd) AS min_price FROM price_history WHERE item_key = ?",
            (item_key,),
        ).fetchone()
        if row and row["min_price"] is not None:
            return float(row["min_price"])
        return None

    def should_notify(self, deal: ScoredDeal, price_drop_renotify_ratio: float = 0.90) -> bool:
        """Return True if this deal (or any merged alias) hasn't been notified yet, or dropped >=10%."""
        notified_rows: list[dict] = []
        for key in self._candidate_keys(deal):
            row = self.get_seen(key)
            if row is not None and row["notified"]:
                notified_rows.append(row)

        if not notified_rows:
            return True

        # Already notified under primary or an alias key: only re-notify if price dropped >= 10%
        # below the lowest previously notified price across matched keys.
        if deal.is_domestic_kr:
            curr_krw = deal.primary.price_krw
            prev_krws = [
                int(r["price_krw"])
                for r in notified_rows
                if r.get("price_krw") is not None and int(r["price_krw"]) > 0
            ]
            if prev_krws and curr_krw is not None:
                if curr_krw <= min(prev_krws) * price_drop_renotify_ratio:
                    return True
            return False

        curr_price = deal.primary.price_usd
        prev_prices = [
            float(r["price_usd"])
            for r in notified_rows
            if r["price_usd"] is not None and float(r["price_usd"]) > 0
        ]
        if prev_prices and curr_price is not None:
            if curr_price <= min(prev_prices) * price_drop_renotify_ratio:
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

    def _upsert_seen_key(
        self,
        key: str,
        deal: ScoredDeal,
        url_for_key: str,
        notified: bool,
        now_dt: datetime,
        now_iso: str,
    ) -> bool:
        """Upsert a single canonical_key into seen_deals when its state materially changed."""
        p = deal.primary
        existing = self.get_seen(key)

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

        if not needs_seen_write:
            return False

        if existing is None:
            self._conn.execute(
                """
                INSERT INTO seen_deals (
                    canonical_key, asin, title, url, price_usd, price_krw, score,
                    notified, first_seen_at, last_seen_at, notified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    p.asin,
                    p.title,
                    url_for_key,
                    stored_price,
                    stored_krw,
                    new_score,
                    new_notified,
                    now_iso,
                    now_iso,
                    new_notified_at,
                ),
            )
        else:
            self._conn.execute(
                """
                UPDATE seen_deals
                SET title = ?, url = ?, price_usd = ?, price_krw = ?, score = ?,
                    notified = ?, last_seen_at = ?, notified_at = ?
                WHERE canonical_key = ?
                """,
                (
                    p.title,
                    url_for_key,
                    stored_price,
                    stored_krw,
                    new_score,
                    new_notified,
                    now_iso,
                    new_notified_at,
                    key,
                ),
            )
        return True

    def record_deal(self, deal: ScoredDeal, notified: bool) -> None:
        """Upsert a deal (and any merged alias URLs when notified) and record changed price_history."""
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        p = deal.primary

        needs_price_history = False
        if p.price_usd is not None:
            latest_price = self._latest_recorded_price(deal.canonical_key)
            needs_price_history = latest_price is None or abs(latest_price - p.price_usd) >= 0.01

        alias_pairs: list[tuple[str, str]] = [(deal.canonical_key, p.url)]
        if notified:
            seen_keys = {deal.canonical_key}
            for url in [p.url, *deal.merged_urls.values()]:
                url_key = make_url_key(url)
                if url_key and url_key not in seen_keys:
                    seen_keys.add(url_key)
                    alias_pairs.append((url_key, url))

        with self._conn:
            for key, key_url in alias_pairs:
                self._upsert_seen_key(key, deal, key_url, notified, now_dt, now_iso)

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
