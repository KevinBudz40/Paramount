"""
Ingest Asana RAID CSV into ChromaDB as structured, queryable chunks.
Each row becomes a human-readable sentence so RAG queries like
"show me OTC items assigned to Kevin" actually work.
"""
import csv
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from rag.store import add_chunks, ingested_sources

CSV_PATH = r"C:\Paramount\todo\Oracle_ERP_-_RAID.csv"
SOURCE_NAME = "Asana_RAID.csv"

FIELDS = [
    ("Name",                    "Task"),
    ("RAID",                    "RAID ID"),
    ("Section/Column",          "Section"),
    ("Assignee",                "Assignee"),
    ("Assignee Email",          "Assignee Email"),
    ("Due Date",                "Due Date"),
    ("Start Date",              "Start Date"),
    ("RAID Type (Oracle)",      "RAID Type"),
    ("Priority (Oracle)",       "Priority"),
    ("RAID Category",           "Category"),
    ("Workstream",              "Workstream"),
    ("ERP Workstream (Oracle)", "ERP Workstream"),
    ("Phase",                   "Phase"),
    ("Boundary System",         "Boundary System"),
    ("Oracle Business Process", "Business Process"),
    ("SubProcess",              "SubProcess"),
    ("% Complete",              "% Complete"),
    ("Escalation (Oracle)",     "Escalation"),
    ("Blocked By (Dependencies)","Blocked By"),
    ("Blocking (Dependencies)", "Blocking"),
    ("Tags",                    "Tags"),
    ("Notes",                   "Notes"),
    ("Parent task",             "Parent Task"),
    ("Projects",                "Project"),
    ("Completed At",            "Completed At"),
    ("Delivery Stage (Oracle)", "Delivery Stage"),
    ("Deep Dive? (Oracle)",     "Deep Dive"),
    ("Oracle's Approach",       "Oracle Approach"),
    ("Closure Category (Oracle)","Closure Category"),
]

def row_to_text(row, headers):
    parts = []
    for col, label in FIELDS:
        if col in headers:
            val = row[headers.index(col)].strip()
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

        for i, row in enumerate(reader):
            if len(row) != len(headers):
                skipped += 1
                continue

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

    # Ingest in batches of 500
    BATCH = 500
    total = 0
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i+BATCH]
        add_chunks(batch)
        total += len(batch)
        print(f"  Ingested {total}/{len(chunks)}...")

    print(f"Done. {total} RAID rows ingested as {SOURCE_NAME}")


if __name__ == "__main__":
    main()
