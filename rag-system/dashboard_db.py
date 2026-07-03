"""Dashboard SQLite database — doc_requests and deliverables tables.

Stored at C:\\GIT\\dashboard.db (one level above rag-system so it
survives app redeployments and is not inside the Flask package).
"""
import sqlite3
from datetime import date, timedelta
from pathlib import Path

_DB = Path(__file__).resolve().parent.parent / "dashboard.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS doc_requests (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                object_name         TEXT NOT NULL,
                doc_type            TEXT NOT NULL DEFAULT 'FDD',
                phase               TEXT NOT NULL DEFAULT '',
                raid_id             TEXT,
                requested_from      TEXT NOT NULL DEFAULT '',
                requested_by        TEXT NOT NULL DEFAULT '',
                date_requested      TEXT NOT NULL,
                date_received       TEXT,
                reviewed            INTEGER NOT NULL DEFAULT 0,
                session_target_date TEXT,
                notes               TEXT
            );

            CREATE TABLE IF NOT EXISTS deliverables (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                deliverable_name TEXT NOT NULL,
                owner            TEXT NOT NULL DEFAULT '',
                workstream       TEXT NOT NULL DEFAULT 'O2C (AR)',
                phase            TEXT NOT NULL DEFAULT '',
                raid_id          TEXT,
                due_date         TEXT,
                status           TEXT NOT NULL DEFAULT 'Not Started',
                blocked_by       TEXT,
                last_updated     TEXT NOT NULL
            );
        """)


# ── Business-day staleness ─────────────────────────────────────────────────

def business_days_since(date_str: str) -> int:
    """Weekday count (Mon–Fri) from date_str up to and including today."""
    try:
        d = date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return 0
    today = date.today()
    if d >= today:
        return 0
    count = 0
    cur = d + timedelta(days=1)
    while cur <= today:
        if cur.weekday() < 5:
            count += 1
        cur += timedelta(days=1)
    return count


def _staleness(bd: int) -> str:
    if bd >= 5:
        return "red"
    if bd >= 3:
        return "amber"
    return "ok"


def _doc_sort_key(x: dict) -> tuple:
    if x["status"] == "Open":
        return ({"red": 0, "amber": 1, "ok": 2}.get(x["staleness"], 3), x["date_requested"] or "")
    return ({"Received": 3, "Ready": 4}.get(x["status"], 5), "")


# ── doc_requests CRUD ──────────────────────────────────────────────────────

def get_phases() -> list[str]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT phase FROM deliverables WHERE phase != '' ORDER BY phase"
        ).fetchall()
    return [r["phase"] for r in rows]


def get_doc_requests(phase: str = "") -> list[dict]:
    init_db()
    with get_conn() as conn:
        if phase:
            rows = conn.execute("""
                SELECT id, object_name, doc_type, phase, raid_id, requested_from, requested_by,
                       date_requested, date_received, reviewed, session_target_date, notes
                FROM doc_requests WHERE phase = ?
            """, (phase,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, object_name, doc_type, phase, raid_id, requested_from, requested_by,
                       date_requested, date_received, reviewed, session_target_date, notes
                FROM doc_requests
            """).fetchall()
    result = []
    for r in rows:
        r = dict(r)
        if r["date_received"] is None:
            r["status"] = "Open"
            bd = business_days_since(r["date_requested"])
            r["staleness_days"] = bd
            r["staleness"] = _staleness(bd)
        elif not r["reviewed"]:
            r["status"] = "Received"
            r["staleness_days"] = 0
            r["staleness"] = "ok"
        else:
            r["status"] = "Ready"
            r["staleness_days"] = 0
            r["staleness"] = "ok"
        result.append(r)
    result.sort(key=_doc_sort_key)
    return result


def add_doc_request(data: dict) -> int:
    init_db()
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO doc_requests
              (object_name, doc_type, phase, raid_id, requested_from, requested_by,
               date_requested, date_received, reviewed, session_target_date, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data["object_name"],
            data.get("doc_type", "FDD"),
            data.get("phase", ""),
            data.get("raid_id"),
            data.get("requested_from", ""),
            data.get("requested_by", ""),
            data["date_requested"],
            data.get("date_received"),
            int(bool(data.get("reviewed", 0))),
            data.get("session_target_date"),
            data.get("notes"),
        ))
        return cur.lastrowid


def patch_doc_request(row_id: int, data: dict):
    init_db()
    allowed = {"date_received", "reviewed", "session_target_date", "notes",
                "requested_from", "requested_by", "object_name", "doc_type"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE doc_requests SET {cols} WHERE id = ?",
            list(updates.values()) + [row_id],
        )


# ── deliverables CRUD ──────────────────────────────────────────────────────

def get_deliverables(phase: str = "") -> list[dict]:
    init_db()
    with get_conn() as conn:
        if phase:
            rows = conn.execute("""
                SELECT id, deliverable_name, owner, workstream, phase, raid_id,
                       due_date, status, blocked_by, last_updated
                FROM deliverables WHERE phase = ?
                ORDER BY workstream,
                         CASE status WHEN 'Blocked' THEN 0 ELSE 1 END,
                         due_date NULLS LAST, deliverable_name
            """, (phase,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, deliverable_name, owner, workstream, phase, raid_id,
                       due_date, status, blocked_by, last_updated
                FROM deliverables
                ORDER BY workstream,
                         CASE status WHEN 'Blocked' THEN 0 ELSE 1 END,
                         due_date NULLS LAST, deliverable_name
            """).fetchall()
    return [dict(r) for r in rows]


def add_deliverable(data: dict) -> int:
    init_db()
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO deliverables
              (deliverable_name, owner, workstream, phase, raid_id, due_date,
               status, blocked_by, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            data["deliverable_name"],
            data.get("owner", ""),
            data.get("workstream", "O2C (AR)"),
            data.get("phase", ""),
            data.get("raid_id"),
            data.get("due_date"),
            data.get("status", "Not Started"),
            data.get("blocked_by"),
            data.get("last_updated", date.today().isoformat()),
        ))
        return cur.lastrowid


def patch_deliverable(row_id: int, data: dict):
    init_db()
    allowed = {"status", "blocked_by", "last_updated", "due_date",
                "owner", "deliverable_name", "workstream"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE deliverables SET {cols} WHERE id = ?",
            list(updates.values()) + [row_id],
        )
