"""
delete_phase2_api.py — Delete Phase 2 chunks via ChromaDB Python API (safe).
[2026-08-09] Created after direct-SQLite method corrupted HNSW index.
Flask must be DOWN. Uses col.delete(where={"source": {"$in": [...]}}) in batches.
"""
import sqlite3
import sys
import time

sys.path.insert(0, r"C:\GIT\rag-system")
sys.path.insert(0, r"C:\ParamountRAG\scripts")

import chromadb
from chromadb.config import Settings
from source_log import init_db, add_skip, remove

PHASE2 = r"C:\Users\kevin\Paramount\Paramount Oracle Assessment - ERP Phase 2 April 2026 Go-Live"
CHROMA_PATH = r"C:\GIT\chroma_db"
SOURCE_LOG = r"C:\GIT\chroma_db\source_log.db"
BATCH_SIZE = 100  # sources per delete call

init_db()

# Load Phase 2 source keys from source_log
con = sqlite3.connect(SOURCE_LOG)
rows = con.execute(
    "SELECT source_key FROM source_log WHERE full_path LIKE ?",
    (PHASE2 + "%",)
).fetchall()
con.close()

phase2_keys = [r[0] for r in rows]
print(f"Phase 2 sources to delete: {len(phase2_keys):,}")
if not phase2_keys:
    print("Nothing to delete.")
    sys.exit(0)

client = chromadb.PersistentClient(
    path=CHROMA_PATH,
    settings=Settings(anonymized_telemetry=False)
)
col = client.get_collection("rag_documents")

print(f"Chunk count before: {col.count():,}")
start = time.time()

deleted_sources = 0
for i in range(0, len(phase2_keys), BATCH_SIZE):
    batch = phase2_keys[i:i + BATCH_SIZE]
    col.delete(where={"source": {"$in": batch}})
    deleted_sources += len(batch)
    elapsed = time.time() - start
    rate = deleted_sources / elapsed if elapsed > 0 else 0
    remaining = len(phase2_keys) - deleted_sources
    eta = remaining / rate if rate > 0 else 0
    print(f"  {deleted_sources:,}/{len(phase2_keys):,} sources deleted | {elapsed:.0f}s elapsed | ETA {eta:.0f}s")

print(f"\nChunk count after: {col.count():,}")

# Clean source_log
con = sqlite3.connect(SOURCE_LOG)
con.execute("DELETE FROM source_log WHERE full_path LIKE ?", (PHASE2 + "%",))
con.commit()
con.close()

# Add Phase 2 root to skip list
add_skip(PHASE2, match_type="folder")

elapsed_total = time.time() - start
print(f"\nDone in {elapsed_total:.0f}s — {len(phase2_keys):,} sources deleted, Phase 2 root skip-listed.")
