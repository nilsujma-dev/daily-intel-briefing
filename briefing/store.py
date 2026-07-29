"""SQLite state: what we've already sent, and per-feed HTTP cache validators."""
from __future__ import annotations
import sqlite3, uuid
from datetime import datetime, timezone, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  url_hash      TEXT NOT NULL,
  guid_hash     TEXT,
  title_hash    TEXT,
  title_norm    TEXT,
  canonical_url TEXT,
  title         TEXT,
  source        TEXT,
  domain        TEXT,
  published_at  TEXT,
  sent_at       TEXT NOT NULL,
  run_id        TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_seen_url  ON seen(url_hash);
CREATE INDEX IF NOT EXISTS ix_seen_guid  ON seen(guid_hash);
CREATE INDEX IF NOT EXISTS ix_seen_title ON seen(title_hash);
CREATE INDEX IF NOT EXISTS ix_seen_sent  ON seen(sent_at);

CREATE TABLE IF NOT EXISTS feed_cache (
  feed_url      TEXT PRIMARY KEY,
  etag          TEXT,
  last_modified TEXT,
  last_ok       TEXT,
  last_status   TEXT,
  fail_streak   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
  run_id      TEXT PRIMARY KEY,
  started_at  TEXT,
  finished_at TEXT,
  status      TEXT,
  cyber_count INTEGER DEFAULT 0,
  ai_count    INTEGER DEFAULT 0,
  suppressed  INTEGER DEFAULT 0,
  detail      TEXT
);
"""


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat() if dt else None


# WAL is the fastest journal mode but needs shared-memory locking that iCloud
# Drive, SMB shares and other synced volumes do not provide - and this app lives
# in ~/Documents, which is frequently iCloud-backed. Degrade until one works
# rather than failing on the user's machine.
JOURNAL_MODES = ("WAL", "TRUNCATE", "PERSIST", "DELETE")


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.journal_mode = self._pick_journal_mode()
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _pick_journal_mode(self):
        for mode in JOURNAL_MODES:
            try:
                active = self.conn.execute("PRAGMA journal_mode=%s" % mode).fetchone()[0]
                # PRAGMA can report success and still fail on first real write.
                self.conn.execute("CREATE TABLE IF NOT EXISTS _probe (x INTEGER)")
                self.conn.execute("INSERT INTO _probe VALUES (1)")
                self.conn.execute("DELETE FROM _probe")
                self.conn.commit()
                return active
            except sqlite3.OperationalError:
                try:
                    self.conn.rollback()
                except sqlite3.Error:
                    pass
                continue
        raise sqlite3.OperationalError(
            "Could not open the state database for writing. If this folder is on "
            "iCloud Drive or a network share, move the app to a local folder.")

    # ---------- runs ----------
    def start_run(self):
        run_id = uuid.uuid4().hex[:12]
        self.conn.execute("INSERT INTO runs(run_id, started_at, status) VALUES (?,?,?)",
                          (run_id, iso(utcnow()), "running"))
        self.conn.commit()
        return run_id

    def finish_run(self, run_id, status, cyber=0, ai=0, suppressed=0, detail=""):
        self.conn.execute(
            "UPDATE runs SET finished_at=?, status=?, cyber_count=?, ai_count=?, suppressed=?, detail=? WHERE run_id=?",
            (iso(utcnow()), status, cyber, ai, suppressed, detail[:2000], run_id))
        self.conn.commit()

    def last_successful_run(self):
        row = self.conn.execute(
            "SELECT started_at FROM runs WHERE status IN ('sent','empty') ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if not row or not row["started_at"]:
            return None
        try:
            return datetime.fromisoformat(row["started_at"])
        except ValueError:
            return None

    # ---------- feed cache ----------
    def feed_cache(self):
        return {r["feed_url"]: dict(r) for r in self.conn.execute("SELECT * FROM feed_cache")}

    def update_feed_cache(self, url, etag, last_modified, status):
        ok = status in ("ok", "not_modified")
        self.conn.execute("""
            INSERT INTO feed_cache(feed_url, etag, last_modified, last_ok, last_status, fail_streak)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(feed_url) DO UPDATE SET
              etag=excluded.etag,
              last_modified=excluded.last_modified,
              last_status=excluded.last_status,
              last_ok=CASE WHEN ? THEN excluded.last_ok ELSE feed_cache.last_ok END,
              fail_streak=CASE WHEN ? THEN 0 ELSE feed_cache.fail_streak + 1 END
        """, (url, etag, last_modified, iso(utcnow()) if ok else None, status, 0 if ok else 1, ok, ok))
        self.conn.commit()

    # ---------- seen ----------
    def is_seen(self, item):
        row = self.conn.execute(
            "SELECT 1 FROM seen WHERE url_hash=? OR (guid_hash=? AND guid_hash IS NOT NULL) "
            "OR (title_hash=? AND title_hash IS NOT NULL) LIMIT 1",
            (item["url_hash"], item["guid_hash"], item["title_hash"])).fetchone()
        return row is not None

    def recent_title_norms(self, days):
        cutoff = iso(utcnow() - timedelta(days=days))
        return [(r["title_norm"] or "", r["title"] or "") for r in self.conn.execute(
            "SELECT title_norm, title FROM seen WHERE sent_at >= ?", (cutoff,))]

    def mark_sent(self, items, run_id):
        now = iso(utcnow())
        rows = [(i["url_hash"], i["guid_hash"], i["title_hash"], i["title_norm"],
                 i["canonical_url"], i["title"], i["source"], i["domain"],
                 iso(i.get("published")), now, run_id) for i in items]
        self.conn.executemany(
            "INSERT OR IGNORE INTO seen(url_hash, guid_hash, title_hash, title_norm, canonical_url,"
            " title, source, domain, published_at, sent_at, run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def prune(self, retention_days):
        cutoff = iso(utcnow() - timedelta(days=retention_days))
        cur = self.conn.execute("DELETE FROM seen WHERE sent_at < ?", (cutoff,))
        self.conn.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,))
        self.conn.commit()
        return cur.rowcount

    def stats(self):
        s = {}
        s["seen_total"] = self.conn.execute("SELECT COUNT(*) c FROM seen").fetchone()["c"]
        s["runs"] = [dict(r) for r in self.conn.execute(
            "SELECT run_id, started_at, status, cyber_count, ai_count, suppressed FROM runs "
            "ORDER BY started_at DESC LIMIT 10")]
        s["feeds"] = [dict(r) for r in self.conn.execute(
            "SELECT feed_url, last_status, last_ok, fail_streak FROM feed_cache ORDER BY feed_url")]
        return s

    def close(self):
        self.conn.close()
