"""Document loading and chunking pipeline."""
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

_7ZIP = r"C:\Program Files\7-Zip\7z.exe"


def _extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """
    Extract a zip archive to dest_dir.
    Tries Python's zipfile first; falls back to 7-Zip for non-standard archives
    (ZIP64 quirks, non-standard compression, recoverable corruption).
    """
    if zipfile.is_zipfile(zip_path):
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(dest_dir)
            return
        except Exception:
            pass  # fall through to 7-zip

    # 7-Zip fallback
    result = subprocess.run(
        [_7ZIP, "x", str(zip_path), f"-o{dest_dir}", "-y", "-bd"],
        capture_output=True, text=True,
    )
    # 7z exit codes: 0=ok, 1=warnings, 2=fatal errors but may have partial output
    # We accept 0-2 and work with whatever was extracted; only hard-fail on 3+
    if result.returncode > 2:
        raise ValueError(
            f"Could not extract {zip_path.name} — "
            f"7-Zip exit {result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
        )

# Binary/image types with no extractable text — skipped silently everywhere
SKIP_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
                   ".mp4", ".mp3", ".wav", ".avi", ".mov", ".zip"}

# All extensions this pipeline can extract text from
INGESTIBLE_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx",
    ".txt", ".md", ".csv", ".svg", ".vtt", ".msg",
}


@dataclass
class ZipReport:
    zip_name: str
    files_found: int = 0
    files_ingested: int = 0
    files_skipped: int = 0
    skipped: list[str] = field(default_factory=list)


def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 150) -> list[str]:
    """Split text into overlapping chunks by sentence boundary."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        slen = len(sentence)
        if current_len + slen > chunk_size and current:
            chunks.append(" ".join(current))
            tail: list[str] = []
            tail_len = 0
            for s in reversed(current):
                if tail_len + len(s) > overlap:
                    break
                tail.insert(0, s)
                tail_len += len(s)
            current = tail
            current_len = tail_len
        current.append(sentence)
        current_len += slen

    if current:
        chunks.append(" ".join(current))

    return [c for c in chunks if len(c.strip()) > 30]


# ── Loaders ───────────────────────────────────────────────────────────────

def load_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def load_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def load_docx(path: Path) -> str:
    from docx import Document
    doc = Document(str(path))
    parts: list[str] = []
    for p in doc.paragraphs:
        if p.text.strip():
            parts.append(p.text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells) + ".")
    return "\n".join(parts)


def load_xls(path: Path) -> str:
    """Extract all sheet content from a legacy .xls workbook using xlrd."""
    import xlrd
    wb = xlrd.open_workbook(str(path))
    parts: list[str] = []
    for sheet_name in wb.sheet_names():
        ws = wb.sheet_by_name(sheet_name)
        rows: list[str] = []
        for i in range(ws.nrows):
            cells = [str(ws.cell_value(i, j)) for j in range(ws.ncols)]
            if any(c.strip() for c in cells):
                rows.append("\t".join(cells))
        if rows:
            parts.append(f"[Sheet: {sheet_name}]\n" + "\n".join(rows))
    return "\n\n".join(parts)


_XLSX_MAX_ROWS = 500  # per sheet — prevents data-dump xlsx files from producing thousands of chunks

def load_xlsx(path: Path) -> str:
    """Extract all sheet content from an Excel workbook as labelled text blocks."""
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    parts: list[str] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows: list[str] = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) if c is not None else "" for c in row]
            if any(c.strip() for c in cells):
                rows.append("\t".join(cells))
            if len(rows) >= _XLSX_MAX_ROWS:
                rows.append(f"[truncated — sheet exceeded {_XLSX_MAX_ROWS} rows]")
                break
        if rows:
            parts.append(f"[Sheet: {sheet_name}]\n" + "\n".join(rows))
    wb.close()
    return "\n\n".join(parts)


def load_pptx(path: Path) -> str:
    """Extract slide text and speaker notes from a PowerPoint file."""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    prs = Presentation(str(path))
    parts: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        lines: list[str] = [f"[Slide {i}]"]
        for shape in slide.shapes:
            try:
                # Tables — extract cell text row by row
                if shape.has_table:
                    for row in shape.table.rows:
                        cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                        if cells:
                            lines.append(" | ".join(cells))
                    continue

                # Charts — extract title only; skip data series
                if shape.shape_type == MSO_SHAPE_TYPE.CHART:
                    try:
                        title = shape.chart.chart_title.text_frame.text.strip()
                        if title:
                            lines.append(f"[Chart] {title}")
                    except Exception:
                        pass
                    continue

                # Regular text frames
                if shape.has_text_frame:
                    tf = shape.text_frame
                    if tf is None:
                        continue
                    text = tf.text
                    if text is None:
                        continue
                    text = text.strip()
                    if text:
                        lines.append(text)
            except Exception:
                continue

        try:
            notes = slide.notes_slide.notes_text_frame.text.strip() if slide.has_notes_slide else ""
            if notes:
                lines.append(f"[Notes] {notes}")
        except Exception:
            pass

        if len(lines) > 1:
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def load_msg(path: Path) -> str:
    """Extract text from an Outlook .msg file."""
    import extract_msg
    msg = extract_msg.Message(str(path))
    parts = []
    if msg.subject:
        parts.append(f"Subject: {msg.subject}")
    if msg.sender:
        parts.append(f"From: {msg.sender}")
    if msg.date:
        parts.append(f"Date: {msg.date}")
    if msg.to:
        parts.append(f"To: {msg.to}")
    if msg.cc:
        parts.append(f"Cc: {msg.cc}")
    if msg.body:
        parts.append(f"\n{msg.body}")
    return "\n".join(parts)


def load_document(path: Path) -> str | None:
    """
    Load a document and return its text content.
    Returns None for silently-skipped binary types (images, video, audio).
    """
    suffix = path.suffix.lower()

    if suffix in SKIP_EXTENSIONS:
        return None

    if suffix == ".pdf":
        return load_pdf(path)
    if suffix in (".docx", ".doc"):
        return load_docx(path)
    if suffix == ".xlsx":
        return load_xlsx(path)
    if suffix == ".xls":
        return load_xls(path)
    if suffix == ".pptx":
        return load_pptx(path)
    if suffix == ".msg":
        return load_msg(path)
    if suffix in (".txt", ".md", ".csv", ".svg", ".vtt"):
        return load_txt(path)

    # Unknown — attempt plain-text fallback
    return load_txt(path)


def chunk_document(path: Path, chunk_size: int = 800, overlap: int = 150) -> list[dict]:
    """
    Chunk a document into overlapping text segments.
    Returns an empty list for silently-skipped file types (images).
    """
    text = load_document(path)
    if not text or not text.strip():
        return []

    chunks = _chunk_text(text, chunk_size, overlap)
    return [
        {"text": chunk, "source": path.name, "chunk_index": i}
        for i, chunk in enumerate(chunks)
    ]


def chunk_zip(
    zip_path: Path,
    chunk_size: int = 800,
    overlap: int = 150,
) -> tuple[list[dict], ZipReport]:
    """
    Extract a zip archive to a temp directory, chunk every supported file,
    and tag each chunk's source as ``{zip_name}/{relative_path_inside_zip}``.

    Returns (all_chunks, ZipReport). The temp directory is always cleaned up.
    """
    report = ZipReport(zip_name=zip_path.name)
    all_chunks: list[dict] = []
    tmp_dir = Path(tempfile.mkdtemp(prefix="rag_zip_"))

    try:
        _extract_zip(zip_path, tmp_dir)

        for file in sorted(tmp_dir.rglob("*")):
            if not file.is_file():
                continue

            rel = file.relative_to(tmp_dir)
            suffix = file.suffix.lower()
            report.files_found += 1

            # Hidden macOS/Windows metadata files
            parts = rel.parts
            if any(p.startswith("__MACOSX") or p.startswith(".") for p in parts):
                report.files_skipped += 1
                report.skipped.append(str(rel))
                continue

            if suffix in SKIP_EXTENSIONS or suffix not in INGESTIBLE_EXTENSIONS:
                report.files_skipped += 1
                report.skipped.append(str(rel))
                continue

            try:
                text = load_document(file)
            except Exception as exc:
                report.files_skipped += 1
                report.skipped.append(f"{rel} ({exc})")
                continue

            if not text or not text.strip():
                report.files_skipped += 1
                report.skipped.append(str(rel))
                continue

            source = f"{zip_path.name}/{rel.as_posix()}"
            chunks = _chunk_text(text, chunk_size, overlap)
            for i, chunk in enumerate(chunks):
                all_chunks.append({"text": chunk, "source": source, "chunk_index": i})

            report.files_ingested += 1

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return all_chunks, report
