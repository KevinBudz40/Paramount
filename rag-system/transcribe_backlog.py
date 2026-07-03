"""
Batch transcribe and ingest all uningested Zoom recordings.
Run: python transcribe_backlog.py
Skips files already in ChromaDB by source name.
"""
import sys, io, os, sqlite3, time, re, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, r"C:\GIT\rag-system")
PERSIST_DIR = r"C:\GIT\chroma_db"
ZOOM_ROOT = r"C:\Users\kevin\OneDrive\Documents\Zoom"

# Already ingested (skip)
SKIP_VTT_SOURCES = {"video1316668366.vtt", "video2535770423.vtt", "video1535770423.vtt"}

def get_ingested_sources():
    db = os.path.join(PERSIST_DIR, "chroma.sqlite3")
    con = sqlite3.connect(db)
    rows = con.execute("SELECT DISTINCT string_value FROM embedding_metadata WHERE key='source'").fetchall()
    con.close()
    return {r[0] for r in rows}

def chunk_text(text, chunk_size=800, overlap=150):
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunk = " ".join(words[i:i+chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return chunks

def add_chunks_direct(chunks, source, meta):
    from rag.store import add_chunks as _add
    chunk_dicts = [{"text": c, "chunk_index": i, "source": source} for i, c in enumerate(chunks)]
    return _add(chunk_dicts, doc_meta=meta, persist_dir=PERSIST_DIR)

def transcribe(mp4_path):
    import whisper
    model = whisper.load_model("small")
    result = model.transcribe(mp4_path, fp16=False, language="en")
    return result

def vtt_source_name(mp4_path):
    base = os.path.splitext(os.path.basename(mp4_path))[0]
    return base + ".vtt"

def folder_label(mp4_path):
    return os.path.basename(os.path.dirname(mp4_path))

def main():
    ingested = get_ingested_sources()
    print(f"Already ingested sources: {len(ingested)}")

    # Collect all MP4s
    mp4s = []
    for root, dirs, files in os.walk(ZOOM_ROOT):
        for f in files:
            if f.lower().endswith(".mp4"):
                mp4s.append(os.path.join(root, f))
    mp4s.sort()

    to_process = []
    for mp4 in mp4s:
        vtt_name = vtt_source_name(mp4)
        if vtt_name in SKIP_VTT_SOURCES or vtt_name in ingested:
            print(f"[SKIP] {vtt_name} already ingested")
            continue
        size_mb = os.path.getsize(mp4) / 1024 / 1024
        if size_mb < 0.5:
            print(f"[SKIP] {os.path.basename(mp4)} too small ({size_mb:.1f} MB)")
            continue
        to_process.append(mp4)

    print(f"\n{len(to_process)} files to transcribe\n")

    for i, mp4 in enumerate(to_process, 1):
        label = folder_label(mp4)
        vtt_name = vtt_source_name(mp4)
        size_mb = os.path.getsize(mp4) / 1024 / 1024
        print(f"[{i}/{len(to_process)}] {label} ({size_mb:.1f} MB) ...", flush=True)
        t0 = time.time()
        try:
            result = transcribe(mp4)
            segments = result.get("segments", [])
            if not segments:
                print(f"  -> 0 segments, skipping")
                continue
            text = " ".join(s["text"].strip() for s in segments if s.get("text","").strip())
            elapsed = time.time() - t0
            print(f"  -> {len(segments)} segments in {elapsed:.0f}s", flush=True)

            chunks = chunk_text(text)
            meta = {
                "type": "meeting_transcript",
                "author": "",
                "date": label[:10],
                "subject": label,
            }
            n = add_chunks_direct(chunks, source=vtt_name, meta=meta)
            print(f"  -> {n} chunks ingested as {vtt_name}", flush=True)
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)

    print("\nDone.")

if __name__ == "__main__":
    main()
