"""
sync_from_asana.py — seed dashboard.db from Asana RICEFW Master CSV export.

Usage:
    python sync_from_asana.py [path_to_ricefw_csv]

Default CSV: C:\\Paramount\\done\\Oracle_ERP_-_RICEFW_Master.csv

Filters to O2C (AR) workstream items only (top-level tasks, no parent task).
Upserts on natural keys so re-running is safe:
  - deliverables: keyed on raid_id
  - doc_requests: keyed on (raid_id, doc_type)
Manual edits to date_received / reviewed / blocked_by (when set by you)
are preserved across re-syncs.

TODO (auto-seed extension point): after a fresh email ingest, scan ChromaDB
for messages matching RAID-\\d+ patterns and doc-request keywords (FDD, FSD,
TDD, "design doc") to auto-populate date_received. Use:
    from rag.store import query
    results = query(f"{raid_id} FDD received", n_results=3, persist_dir=...)
    if results: patch_doc_request(id, {"date_received": results[0]["date"]})
"""
import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dashboard_db import get_conn, init_db

DEFAULT_CSV = Path(r"C:\Paramount\done\Oracle_ERP_-_RICEFW_Master.csv")

# Workstreams to include — all variants that map to Kevin's O2C/AR area
_OTC_WORKSTREAMS = {"O2C (AR)", "O2C (AR), P2R (PPM)", "O2C (AR), RTR (FAH)", "O2C (AR), RTR (GL)"}

# RICEFW status → dashboard status
_STATUS_MAP = {
    "not started":                                           "Not Started",
    "functional design - 25% dev complete":                  "In Progress",
    "technical design - 75% dev complete":                   "In Progress",
    "fdd business approved - 50% dev complete":              "In Progress",
    "development":                                           "In Progress",
    "delivered - 50% (out of the box - testing still needed)": "In Progress",
    "conversion - in test load cycle":                       "In Progress",
    "conversion - prod load":                                "In Progress",
    "conversion - ready for extract":                        "In Progress",
    "functional testing - 100% dev complete":                "Client Review",
    "integration/report user testing":                       "Client Review",
    "integration/report user testing completed":             "Client Review",
    "ready for business approval":                           "Client Review",
    "waiting on functional dependency":                      "Blocked",
    "closed":                                                "Done",
    "in production":                                         "Done",
    "integration/report - ready for production":             "Done",
}
# Skip entirely — not relevant to active tracking
_SKIP = {"deferred", "out of scope"}


def _map_status(raw: str) -> str | None:
    key = raw.strip().lower()
    if key in _SKIP:
        return None
    return _STATUS_MAP.get(key, "Not Started")


def sync(csv_path: Path):
    init_db()
    today = date.today().isoformat()

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        all_rows = list(csv.DictReader(f))

    items = [
        r for r in all_rows
        if not r.get("Parent task", "").strip()
        and any(ws in r.get("ERP Workstream (Oracle)", "") for ws in _OTC_WORKSTREAMS)
        and r.get("Name", "").strip()
        and r.get("RAID", "").strip()
    ]

    print(f"Found {len(items)} O2C (AR) top-level items in {csv_path.name}")

    new_del = 0
    upd_del = 0
    new_doc = 0
    upd_doc = 0
    skipped = 0

    with get_conn() as conn:
        for r in items:
            raid_id   = r["RAID"].strip()
            name      = r["Name"].strip()
            fdd_due   = r.get("FDD Due Date", "").strip() or None
            tdd_due   = r.get("TDD Due Date", "").strip() or None
            raw_status = r.get("Status (Oracle)", "").strip()
            status    = _map_status(raw_status)
            dep_raw   = r.get("Blocked By (Dependencies)", "").strip() or None
            last_mod  = (r.get("Last Modified", "").strip() or today)[:10]
            pg_owner  = (r.get("PG Tech Owner", "").strip()
                         or r.get("PG Business Owner", "").strip())
            ocs_owner = (r.get("OCS Functional Owner", "").strip()
                         or r.get("OCS Tech Owner", "").strip())
            due_date  = (r.get("Due Date", "").strip()
                         or fdd_due or tdd_due or None)
            phase     = r.get("Phase (Oracle)", "").strip()

            # ── deliverables ──────────────────────────────────────────────
            if status is None:
                skipped += 1
            else:
                existing = conn.execute(
                    "SELECT id, blocked_by FROM deliverables WHERE raid_id = ?", (raid_id,)
                ).fetchone()
                if existing:
                    conn.execute("""
                        UPDATE deliverables
                        SET deliverable_name=?, owner=?, workstream=?, phase=?,
                            due_date=?, status=?, last_updated=?
                        WHERE raid_id=?
                    """, (name, pg_owner, "O2C (AR)", phase, due_date, status, last_mod, raid_id))
                    # Sync dep from Asana only when CSV has a value — never erase manual notes
                    if dep_raw:
                        conn.execute(
                            "UPDATE deliverables SET blocked_by=? WHERE raid_id=?",
                            (dep_raw, raid_id)
                        )
                    elif status == "Blocked" and existing["blocked_by"] is None:
                        conn.execute(
                            "UPDATE deliverables SET blocked_by='[check Asana]' WHERE raid_id=?",
                            (raid_id,)
                        )
                    upd_del += 1
                else:
                    placeholder = dep_raw or ("[check Asana]" if status == "Blocked" else None)
                    conn.execute("""
                        INSERT INTO deliverables
                          (deliverable_name, owner, workstream, phase, raid_id,
                           due_date, status, blocked_by, last_updated)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (name, pg_owner, "O2C (AR)", phase, raid_id,
                          due_date, status, placeholder, last_mod))
                    new_del += 1

            # ── doc_requests (items with FDD or TDD due date) ─────────────
            for doc_type, target_date in [("FDD", fdd_due), ("TDD", tdd_due)]:
                if not target_date:
                    continue
                existing = conn.execute(
                    "SELECT id FROM doc_requests WHERE raid_id=? AND doc_type=?",
                    (raid_id, doc_type),
                ).fetchone()
                if existing:
                    conn.execute("""
                        UPDATE doc_requests
                        SET object_name=?, requested_from=?,
                            requested_by=?, session_target_date=?
                        WHERE raid_id=? AND doc_type=?
                    """, (name, ocs_owner, pg_owner, target_date, raid_id, doc_type))
                    upd_doc += 1
                else:
                    conn.execute("""
                        INSERT INTO doc_requests
                          (object_name, doc_type, phase, raid_id, requested_from, requested_by,
                           date_requested, date_received, reviewed, session_target_date)
                        VALUES (?,?,?,?,?,?,?,NULL,0,?)
                    """, (name, doc_type, phase, raid_id, ocs_owner, pg_owner, today, target_date))
                    new_doc += 1

    print(f"Deliverables : {new_del} inserted, {upd_del} updated, {skipped} skipped (deferred/OOS)")
    print(f"Doc requests : {new_doc} inserted, {upd_doc} updated")
    print()
    print("NOTE: date_requested defaults to today for new doc_request rows.")
    print("      Update per-item once you know when you actually asked for each doc.")


if __name__ == "__main__":
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}")
        sys.exit(1)
    sync(csv_path)
