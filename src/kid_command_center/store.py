"""Everything the app keeps, in one SQLite file in the data folder.

    sessions  foreground time: one row per stretch of time in one app or site
    opens     each time an app or site was opened (the "clean rows")
    rules     blocks (kill switch) and daily limits set by the parent
    usage     seconds used today per limit rule; extra minutes the parent granted
    events    blocks enforced, warnings, monitor start/stop and gaps
    meta      password hash and the monitor's heartbeat
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,           -- local date, YYYY-MM-DD
    start REAL NOT NULL,
    end REAL NOT NULL,
    kind TEXT NOT NULL,          -- app | site
    key TEXT NOT NULL,           -- exe name (app) or domain (site)
    exe TEXT NOT NULL,           -- the program in front (for a site: the browser)
    title TEXT,                  -- the window title last seen
    url TEXT                     -- the page last seen (sites)
);
CREATE INDEX IF NOT EXISTS idx_sessions_day ON sessions(day, kind, key);
CREATE TABLE IF NOT EXISTS opens (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    exe TEXT NOT NULL,
    title TEXT,
    url TEXT
);
CREATE INDEX IF NOT EXISTS idx_opens_day ON opens(day, ts);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,          -- app | site
    target TEXT NOT NULL,        -- exe name, e.g. roblox.exe, or a domain, e.g. youtube.com
    browser TEXT,                -- sites only: limit to one browser (e.g. chrome); NULL = any
    mode TEXT NOT NULL,          -- block | limit
    minutes INTEGER,             -- limit: minutes per day
    enabled INTEGER NOT NULL DEFAULT 1,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    day TEXT NOT NULL,
    rule_id INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    seconds REAL NOT NULL DEFAULT 0,
    extra_minutes INTEGER NOT NULL DEFAULT 0,
    warned INTEGER NOT NULL DEFAULT 0,   -- minutes-left warnings already shown (bit flags)
    PRIMARY KEY (day, rule_id)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,          -- blocked | limit_reached | warning | monitor_start | monitor_stop | gap
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_day ON events(day, ts);
"""


def data_dir() -> Path:
    base = os.environ.get("KCC_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA", str(Path.home())), "KidCommandCenter")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


class Store:
    """One connection per thread (the monitor and the web page run on different threads);
    writes are serialised by a lock."""

    def __init__(self, path: Path | str | None = None):
        self.path = str(path or data_dir() / "activity.db")
        self.lock = threading.RLock()
        self._local = threading.local()
        with self.lock, self.conn:
            self.conn.executescript(SCHEMA)

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=10)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
        return c

    def write(self, sql: str, params=()) -> int:
        with self.lock, self.conn:
            return self.conn.execute(sql, params).lastrowid

    # ---- meta ---------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.write("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, value))

    # ---- activity -----------------------------------------------------------
    def add_session(self, s) -> int:
        return self.write(
            "INSERT INTO sessions(day,start,end,kind,key,exe,title,url) VALUES(?,?,?,?,?,?,?,?)",
            (day_of(s.start), s.start, s.end, s.kind, s.key, s.exe, s.title, s.url))

    def extend_session(self, sid: int, s) -> None:
        self.write("UPDATE sessions SET end=?, title=?, url=? WHERE id=?", (s.end, s.title, s.url, sid))

    def add_open(self, ts: float, kind: str, key: str, exe: str, title: str | None, url: str | None) -> None:
        self.write("INSERT INTO opens(ts,day,kind,key,exe,title,url) VALUES(?,?,?,?,?,?,?)",
                   (ts, day_of(ts), kind, key, exe, title, url))

    def event(self, kind: str, detail: str = "", ts: float | None = None) -> None:
        ts = ts or time.time()
        self.write("INSERT INTO events(ts,day,kind,detail) VALUES(?,?,?,?)", (ts, day_of(ts), kind, detail))

    # ---- reports --------------------------------------------------------------
    def summary(self, day: str) -> list[dict]:
        """One row per app or site used that day: times opened, time in front, first and last."""
        rows: dict[tuple, dict] = {}
        for r in self.conn.execute(
                "SELECT kind, key, SUM(end - start) secs, MIN(start) first, MAX(end) last "
                "FROM sessions WHERE day=? GROUP BY kind, key", (day,)):
            rows[(r["kind"], r["key"])] = {"kind": r["kind"], "key": r["key"], "opens": 0,
                                           "seconds": round(r["secs"]), "first": r["first"],
                                           "last": r["last"]}
        for r in self.conn.execute(
                "SELECT kind, key, COUNT(*) n, MIN(ts) first, MAX(ts) last FROM opens WHERE day=? "
                "GROUP BY kind, key", (day,)):
            row = rows.setdefault((r["kind"], r["key"]), {
                "kind": r["kind"], "key": r["key"], "opens": 0, "seconds": 0,
                "first": r["first"], "last": r["last"]})
            row["opens"] = r["n"]
            row["first"] = min(row["first"], r["first"])
            row["last"] = max(row["last"] or 0, r["last"])
        for (kind, key), row in rows.items():
            last = self.conn.execute(
                "SELECT title, url, exe FROM sessions WHERE day=? AND kind=? AND key=? "
                "ORDER BY end DESC LIMIT 1", (day, kind, key)).fetchone() or self.conn.execute(
                "SELECT title, url, exe FROM opens WHERE day=? AND kind=? AND key=? "
                "ORDER BY ts DESC LIMIT 1", (day, kind, key)).fetchone()
            row.update(title=last["title"], url=last["url"], exe=last["exe"])
        return sorted(rows.values(), key=lambda r: (-r["seconds"], -r["opens"]))

    def opens_list(self, day: str, limit: int = 500) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT ts, kind, key, exe, title, url FROM opens WHERE day=? ORDER BY ts DESC LIMIT ?",
            (day, limit))]

    def events_list(self, day: str, limit: int = 200) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT ts, kind, detail FROM events WHERE day=? ORDER BY ts DESC LIMIT ?", (day, limit))]

    def days(self, limit: int = 60) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT DISTINCT day FROM sessions UNION SELECT DISTINCT day FROM opens ORDER BY 1 DESC LIMIT ?",
            (limit,))]

    # ---- rules ------------------------------------------------------------------
    def rules(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM rules ORDER BY created")]

    def add_rule(self, kind: str, target: str, mode: str, minutes: int | None = None,
                 browser: str | None = None) -> int:
        return self.write(
            "INSERT INTO rules(kind,target,browser,mode,minutes,enabled,created) VALUES(?,?,?,?,?,1,?)",
            (kind, target, browser, mode, minutes, time.time()))

    def update_rule(self, rid: int, **fields) -> None:
        allowed = {"enabled", "minutes", "mode", "browser"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if sets:
            self.write(f"UPDATE rules SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                       (*sets.values(), rid))

    def delete_rule(self, rid: int) -> None:
        self.write("DELETE FROM rules WHERE id=?", (rid,))

    def usage(self, day: str) -> dict[int, dict]:
        return {r["rule_id"]: dict(r) for r in self.conn.execute("SELECT * FROM usage WHERE day=?", (day,))}

    def add_usage(self, day: str, rule_id: int, seconds: float) -> None:
        self.write("INSERT INTO usage(day,rule_id,seconds) VALUES(?,?,?) "
                   "ON CONFLICT(day,rule_id) DO UPDATE SET seconds=seconds+excluded.seconds",
                   (day, rule_id, seconds))

    def grant_extra(self, day: str, rule_id: int, minutes: int) -> None:
        self.write("INSERT INTO usage(day,rule_id,extra_minutes) VALUES(?,?,?) "
                   "ON CONFLICT(day,rule_id) DO UPDATE SET extra_minutes=extra_minutes+excluded.extra_minutes",
                   (day, rule_id, minutes))

    def mark_warned(self, day: str, rule_id: int, flag: int) -> None:
        self.write("INSERT INTO usage(day,rule_id,warned) VALUES(?,?,?) "
                   "ON CONFLICT(day,rule_id) DO UPDATE SET warned=warned|excluded.warned",
                   (day, rule_id, flag))


def today() -> str:
    return date.today().strftime("%Y-%m-%d")
