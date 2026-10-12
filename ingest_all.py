# ingest_all.py
# [2026-08-24] Master ingest runner — sequential (Qdrant single-writer lock).
# Order: sync emails → ingest emails → ingest local docs/phase4a+4b (runs label_chunks internally).
# Tactiq meetings are NOT automated here — they require MCP tool calls (do those in-session).
# [2026-10-12] Added Step 4: Slack ingest (ingest_slack.py --apply) from C:\ParamountRAG\slack\*.json.
#   The Slack *export* still requires MCP tool calls in-session; this step only ingests the JSON files
#   already on disk (idempotent — re-ingesting unchanged files just re-upserts the same chunk IDs).
#
# Usage:
#   python C:\GIT\ingest_all.py
#   python C:\GIT\ingest_all.py --skip-email   # skip Outlook sync + email ingest
#   python C:\GIT\ingest_all.py --skip-sync    # skip Outlook sync only (still ingests emails.json)
#   python C:\GIT\ingest_all.py --skip-slack   # [2026-10-12] skip Slack ingest

import argparse
import subprocess
import sys
from datetime import datetime

PYTHON = sys.executable

SYNC_OUTLOOK   = r"C:\ParamountRAG\scripts\sync_outlook.py"
INGEST_EMAILS  = r"C:\GIT\ingest_emails_batched.py"
INGEST_LOCAL   = r"C:\GIT\ingest_phase4a_4b_local.py"
INGEST_SLACK   = r"C:\GIT\ingest_slack.py"  # [2026-10-12] added


def run(label, cmd):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"\n[WARNING] '{label}' exited with code {result.returncode} — continuing.")
    return result.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-email", action="store_true", help="Skip Outlook sync and email ingest entirely")
    parser.add_argument("--skip-sync",  action="store_true", help="Skip Outlook sync but still ingest emails.json")
    parser.add_argument("--skip-slack", action="store_true", help="Skip Slack ingest")  # [2026-10-12] added
    args = parser.parse_args()

    print(f"\nIngest All — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("NOTE: Qdrant single-writer lock — steps run sequentially.\n")

    codes = []

    if not args.skip_email:
        if not args.skip_sync:
            # [2026-10-12] step labels renumbered /3 → /4 for the new Slack step
            codes.append(run("Step 1/4: Sync Outlook email (incremental)", [PYTHON, SYNC_OUTLOOK, "--mail"]))
        else:
            print("\n[Step 1/4: Sync Outlook — SKIPPED (--skip-sync)]")

        codes.append(run("Step 2/4: Ingest emails → Qdrant", [PYTHON, INGEST_EMAILS]))
    else:
        print("\n[Steps 1-2: Email sync + ingest — SKIPPED (--skip-email)]")

    codes.append(run("Step 3/4: Ingest local docs (Phase 4a/4b) + label_chunks", [PYTHON, INGEST_LOCAL]))

    # [2026-10-12] added Step 4: Slack ingest — runs last, after label_chunks has released the Qdrant lock.
    # Sets doc_type="Slack" itself, so label_chunks doesn't need to run again.
    if not args.skip_slack:
        codes.append(run("Step 4/4: Ingest Slack exports → Qdrant", [PYTHON, INGEST_SLACK, "--apply"]))
    else:
        print("\n[Step 4/4: Slack ingest — SKIPPED (--skip-slack)]")

    print(f"\n{'='*60}")
    failures = [i+1 for i, c in enumerate(codes) if c != 0]
    if failures:
        print(f"  Done with warnings — steps with non-zero exit: {failures}")
    else:
        print(f"  All steps completed successfully.")
    print(f"  {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
