"""
Ingest Asana RICEFW Master CSV into ChromaDB as structured, queryable chunks.
Each row becomes a human-readable sentence so queries like
"show me OTC integrations assigned to Kevin" actually work.
"""
import csv
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from rag.store import add_chunks, ingested_sources

CSV_PATH = r"C:\Paramount\todo\Oracle_ERP_-_RICEFW_Master (3).csv"
SOURCE_NAME = "Asana_RICEFW_Master.csv"

FIELDS = [
    ("Name",                            "Task"),
    ("Object ID (Oracle)",              "Object ID"),
    ("Integration Unit",                "Integration Unit"),
    ("Section/Column",                  "Section"),
    ("Assignee",                        "Assignee"),
    ("Assignee Email",                  "Assignee Email"),
    ("Due Date",                        "Due Date"),
    ("Start Date",                      "Start Date"),
    ("PG Business Owner",               "PG Business Owner"),
    ("PG Tech Owner",                   "PG Tech Owner"),
    ("OCS Functional Owner",            "OCS Functional Owner"),
    ("OCS Tech Owner",                  "OCS Tech Owner"),
    ("Boundary System",                 "Boundary System"),
    ("Boundary System Owner",           "Boundary System Owner"),
    ("Phase (Oracle)",                  "Phase"),
    ("Status (Oracle)",                 "Status"),
    ("Object Type (Oracle)",            "Object Type"),
    ("ERP Workstream (Oracle)",         "ERP Workstream"),
    ("Priority (Oracle)",               "Priority"),
    ("Direction (Fusion Centric) INT",  "Direction"),
    ("Frequency (INT)",                 "Frequency"),
    ("Domestic/ International (INT)",   "Domestic/International"),
    ("Revenue Area (OTC)",              "Revenue Area"),
    ("RAID",                            "RAID"),
    ("Global RICEFW ID",                "Global RICEFW ID"),
    ("Legacy RICEFW ID",                "Legacy RICEFW ID"),
    ("Legacy ERP",                      "Legacy ERP"),
    ("Delivery Stage (Oracle)",         "Delivery Stage"),
    ("Design Cycle (Oracle)",           "Design Cycle"),
    ("Build Cycle",                     "Build Cycle"),
    ("FDD % Complete",                  "FDD % Complete"),
    ("TDD % Complete",                  "TDD % Complete"),
    ("Build-UT % Complete (Oracle)",    "Build % Complete"),
    ("Tech Validation",                 "Tech Validation"),
    ("Status Roll-up (Int.)",           "Status Roll-up"),
    ("PG RTR Contact",                  "PG RTR Contact"),
    ("Tax Calculation",                 "Tax Calculation"),
    ("Bifurcation (Interfaces)",        "Bifurcation"),
    ("Blocked By (Dependencies)",       "Blocked By"),
    ("Blocking (Dependencies)",         "Blocking"),
    ("Tags",                            "Tags"),
    ("Summary Comments",                "Summary Comments"),
    ("Notes",                           "Notes"),
    ("Parent task",                     "Parent Task"),
    ("Completed At",                    "Completed At"),
    ("Reuse Integration build in previous Phase", "Reuse from Previous Phase"),
]


def row_to_text(row, headers):
    parts = []
    for col, label in FIELDS:
        if col in headers:
            idx = headers.index(col)
            if idx < len(row):
                val = row[idx].strip()
                if val:
                    parts.append(f"{label}: {val}")
    return " | ".join(parts) if parts else None


def main():
    if SOURCE_NAME in ingested_sources():
        print(f"[SKIP] {SOURCE_NAME} already ingested. Delete from ChromaDB to re-ingest.")
        return

    chunks = []
    skipped = 0

    with open(CSV_PATH, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader)
        # strip whitespace from headers
        headers = [h.strip() for h in headers]

        for i, row in enumerate(reader):
            text = row_to_text(row, headers)
            if not text or len(text) < 20:
                skipped += 1
                continue

            chunks.append({
                "source": SOURCE_NAME,
                "chunk_index": i,
                "text": text,
            })

    print(f"Rows processed: {len(chunks)} | Skipped: {skipped}")

    BATCH = 500
    total = 0
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i + BATCH]
        add_chunks(batch)
        total += len(batch)
        print(f"  Ingested {total}/{len(chunks)}...")

    print(f"Done. {total} RICEFW rows ingested as {SOURCE_NAME}")


if __name__ == "__main__":
    main()
