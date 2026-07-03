"""
Transcribe and ingest MP4s from C:\Paramount\Meetings that aren't already in ChromaDB.
"""
import sys, io, os, sqlite3, time, pathlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, r"C:\GIT\rag-system")
PERSIST_DIR = r"C:\GIT\chroma_db"
MEETINGS_ROOT = r"C:\Paramount\Meetings"
MIN_SIZE_MB = 0.5
MAX_SIZE_MB = 500  # skip files larger than this

from rag.chunker import _chunk_text
from rag.store import add_chunks


def get_ingested_sources():
    db = os.path.join(PERSIST_DIR, "chroma.sqlite3")
    con = sqlite3.connect(db)
    rows = con.execute("SELECT DISTINCT string_value FROM embedding_metadata WHERE key='source'").fetchall()
    con.close()
    return {r[0] for r in rows}


def transcribe(mp4_path):
    import whisper
    model = whisper.load_model("small")
    result = model.transcribe(str(mp4_path))
    segments = result.get("segments", [])
    print(f"   -> {len(segments)} segments")
    lines = []
    for seg in segments:
        start = int(seg["start"])
        h, m, s = start // 3600, (start % 3600) // 60, start % 60
        lines.append(f"[{h:02d}:{m:02d}:{s:02d}] {seg['text'].strip()}")
    return "\n".join(lines)


def main():
    ingested = get_ingested_sources()
    print(f"Already ingested sources: {len(ingested)}")

    mp4s = sorted(pathlib.Path(MEETINGS_ROOT).rglob("*.mp4"))
    todo = []
    for mp4 in mp4s:
        size_mb = mp4.stat().st_size / 1024 / 1024
        source_name = mp4.name.replace(".mp4", ".vtt")
        if source_name in ingested or mp4.name in ingested:
            print(f"[SKIP] {mp4.name} already ingested")
        elif size_mb < MIN_SIZE_MB:
            print(f"[SKIP] {mp4.name} too small ({size_mb:.1f} MB)")
        elif size_mb > MAX_SIZE_MB:
            print(f"[SKIP] {mp4.name} too large ({size_mb:.1f} MB) - run separately")
        else:
            todo.append((mp4, size_mb, source_name))

    print(f"\n{len(todo)} files to transcribe\n")

    for i, (mp4, size_mb, source_name) in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {mp4.name} ({size_mb:.1f} MB) ...")
        t0 = time.time()
        try:
            text = transcribe(mp4)
            elapsed = int(time.time() - t0)
            chunks = _chunk_text(text, source_name)
            for j, c in enumerate(chunks):
                c["source"] = source_name
                c["chunk_index"] = j
            add_chunks(chunks)
            print(f"   -> {len(chunks)} chunks ingested as {source_name} in {elapsed}s")
        except Exception as e:
            print(f"   ERROR: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
