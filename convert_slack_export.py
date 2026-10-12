# convert_slack_export.py
# [2026-10-12] NEW: convert a Slack "partial archive" zip (messages.jsonl + coverage.json) into the
# per-conversation JSON format read by ingest_slack.py.  Reads the zip in place — no extraction.
#
# First used for the Oracle One export (slack_messages_partial_2026-10-12.zip) — the workspace the
# Slack MCP connector cannot reach.  Output files are prefixed "oracle-one-" so source keys
# (slack:oracle-one-<conv>:<date>) never collide with Skydance conversations.
#
# Scope (agreed with Kevin 2026-10-12):
#   - project channels in INCLUDE_CHANNELS
#   - every DM / Group DM with content (bots + empty ones dropped)
#   - Oracle corporate channels (#it-support, #oracle-news, #general, #css-na, ...) EXCLUDED as noise
#   - nothing before 2026-06-01
#
# Usage:
#   python -I C:\GIT\convert_slack_export.py <zip> [--out C:\ParamountRAG\slack] [--dry-run]

import argparse, json, re, sys, zipfile
from collections import defaultdict
from pathlib import Path

CUTOFF   = "2026-06-01"
PREFIX   = "oracle-one-"
WORKSPACE = "Oracle One"

INCLUDE_CHANNELS = {
    "C093E27BFMY",  # #parmaount-datamigration-ar
    "C0BCZKTQZGR",  # #paramount-o2c-internal
    "C0BJD1U5N87",  # #psky-phase4-customers-conversion
    "C0BPPML1ZFS",  # #psky_45_ppc_viacom
    "C0C0EG5CCBT",  # #pg_o2c_functional
    "C0BQSMTPHFS",  # #pg_o2c_integration_internal_functional
}
BOT_AUTHORS = ("Slackbot", "ChatGPT", "Welcome New Members", "Slack")
KEVIN = "Kevin Budziszewski"

_MENTION  = re.compile(r"<@[A-Z0-9]+\|([^>]+)>")
_MENTION2 = re.compile(r"<@([A-Z0-9]+)>")
_SPECIAL  = re.compile(r"<!(here|channel|everyone)[^>]*>")
_LINK     = re.compile(r"<(https?://[^|>]+)\|([^>]+)>")
_BARELINK = re.compile(r"<(https?://[^>]+)>")
_MAILTO   = re.compile(r"<mailto:[^|>]+\|([^>]+)>")
_JOINED   = re.compile(r"has (joined|left) the (channel|conversation)\s*$")
_META     = re.compile(r"^(Reactions|Thread|Message TS|Link|Attachment): ")
_FWD_TAIL = re.compile(r"\s*\S*\s*use slack_read_thread or slack_read_channel to read the original message\s*$")


def clean(text: str) -> str:
    text = _MENTION.sub(r"@\1", text)
    text = _MENTION2.sub(r"@\1", text)
    text = _SPECIAL.sub(r"@\1", text)
    text = _MAILTO.sub(r"\1", text)
    text = _LINK.sub(r"\2", text)
    text = _BARELINK.sub(r"[link]", text)
    lines = [l for l in text.splitlines() if not _META.match(l)]
    text = "\n".join(lines)
    text = _FWD_TAIL.sub("", text)
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()


def author_name(raw: str) -> str:
    return raw.split("<")[0].strip() or raw.strip()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zip")
    ap.add_argument("--out", default=r"C:\ParamountRAG\slack")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    z = zipfile.ZipFile(args.zip)
    cov = {c["id"]: c for c in json.loads(z.read("coverage.json"))}

    by_conv: dict[str, list[dict]] = defaultdict(list)
    for line in z.open("messages.jsonl"):
        m = json.loads(line)
        cid = m["conversation_id"]
        ctype = cov.get(cid, {}).get("type", "")
        if cid not in INCLUDE_CHANNELS and ctype not in ("Direct Message", "Group DM"):
            continue
        dt = m["displayed_time"][:19]          # local (ET) time, same convention as Skydance exports
        if dt[:10] < CUTOFF:
            continue
        author = author_name(m["author"])
        if author.startswith(BOT_AUTHORS) or author == "":
            continue
        body = m["returned_message"] or ""
        if _JOINED.search(body):
            continue
        text = clean(body)
        if not text:
            continue
        by_conv[cid].append({"ts": m["message_ts"], "parent": m["thread_parent_ts"],
                             "dt": dt, "author": author, "text": text})

    out = Path(args.out)
    total_msgs = 0
    for cid, msgs in sorted(by_conv.items(), key=lambda kv: -len(kv[1])):
        c = cov[cid]
        authors = sorted({m["author"] for m in msgs})
        others = [a for a in authors if a != KEVIN]
        if c["type"] == "Direct Message":
            if not others:
                continue                          # self-DM
            name, ctype = f"dm-{slug(others[0])}", "dm"
        elif c["type"] == "Group DM":
            name, ctype = f"gdm-{cid.lower()}", "group_dm"
        else:
            name = slug(c["name"].lstrip("#"))
            ctype = "private_channel" if "Private" in c["type"] else "public_channel"

        # attach thread replies under their parent (replies keep their own text; parent day governs)
        top, replies = [], defaultdict(list)
        for m in sorted(msgs, key=lambda m: m["dt"]):
            if m["parent"] and m["parent"] != m["ts"]:
                replies[m["parent"]].append(m)
            else:
                top.append(m)
        top_ts = {m["ts"] for m in top}
        out_msgs = []
        for m in top:
            rec = {"ts": m["ts"], "dt": m["dt"], "author": m["author"], "text": m["text"]}
            if replies.get(m["ts"]):
                rec["replies"] = [{"author": r["author"], "text": r["text"]} for r in replies[m["ts"]]]
            out_msgs.append(rec)
        for pts, rs in replies.items():          # orphan replies (parent before cutoff / not retrieved)
            if pts not in top_ts:
                for r in rs:
                    out_msgs.append({"ts": r["ts"], "dt": r["dt"], "author": r["author"],
                                     "text": "(thread reply) " + r["text"]})
        out_msgs.sort(key=lambda m: m["dt"])

        doc = {
            "conversation": PREFIX + name,
            "channel_id": cid,
            "type": ctype,
            "workspace": WORKSPACE,
            "members_note": f"{c['name']} — participants since {CUTOFF}: {', '.join(authors)}",
            "exported_at": "2026-10-12",
            "messages": out_msgs,
        }
        total_msgs += len(msgs)
        fname = out / f"{PREFIX}{name}.json"
        print(f"  {len(msgs):5d} msgs  {fname.name}")
        if not args.dry_run:
            fname.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"\n{len(by_conv)} conversations, {total_msgs} messages"
          + (" (DRY RUN - nothing written)" if args.dry_run else f" -> {out}"))  # ASCII only: cp1252 console


if __name__ == "__main__":
    main()
