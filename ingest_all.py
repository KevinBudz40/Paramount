# ingest_all.py
# [2026-08-24] Master ingest runner — sequential (Qdrant single-writer lock).
# Order: sync emails → ingest emails → ingest local docs/phase4a+4b (runs label_chunks internally).
# Tactiq meetings are NOT automated here — they require MCP tool calls (do those in-session).
#
# Usage:
#   python C:\GIT\ingest_all.py
#   python C:\GIT\ingest_all.py --skip-email   # skip Outlook sync + email ingest
#   python C:\GIT\ingest_all.py --skip-sync    # skip Outlook sync only (still ingests emails.json)

import argparse
import subprocess
import sys
from datetime import datetime

PYTHON = sys.executable

SYNC_OUTLOOK   = r"C:\ParamountRAG\scripts\sync_outlook.py"
INGEST_EMAILS  = r"C:\GIT\ingest_emails_batched.py"
INGEST_LOCAL   = r"C:\GIT\ingest_phase4a_4b_local.py"


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
    args = parser.parse_args()

    print(f"\nIngest All — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("NOTE: Qdrant single-writer lock — steps run sequentially.\n")

    codes = []

    if not args.skip_email:
        if not args.skip_sync:
            codes.append(run("Step 1/3: Sync Outlook email (incremental)", [PYTHON, SYNC_OUTLOOK, "--mail"]))
        else:
            print("\n[Step 1/3: Sync Outlook — SKIPPED (--skip-sync)]")

        codes.append(run("Step 2/3: Ingest emails → Qdrant", [PYTHON, INGEST_EMAILS]))
    else:
        print("\n[Steps 1-2: Email sync + ingest — SKIPPED (--skip-email)]")

    codes.append(run("Step 3/3: Ingest local docs (Phase 4a/4b) + label_chunks", [PYTHON, INGEST_LOCAL]))

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
