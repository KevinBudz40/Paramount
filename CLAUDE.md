# Claude Code — C:\GIT project instructions

## Todo scanner workflow

When the user says **"scan my todos"**, **"scan for new tasks"**, or similar:

1. Load the scan cursor from `C:\GIT\todo_cursor.json` to determine the last scan timestamp per source.
   If no cursor exists, default to **7 days ago**.

2. **Tactiq meetings** — list recent meetings using the Tactiq MCP tool (`list_recent_meetings`),
   filtered to meetings Kevin attended since the cursor. For each meeting, call `get_meeting`
   and read `detailedSummary.content`. Extract Kevin-specific action items inline.

3. **Outlook email** — read `C:\ParamountRAG\outlook\email\emails.json`. Filter to emails
   with `received_time` >= cursor email timestamp. For each email, extract Kevin action items
   from subject + body.

4. **Slack** — two passes:

   a. **Mentions search** — run `slack_search_public_and_private` twice:
      - `to:<@U0BA98BHRSQ> after:<date>` (user ID mentions)
      - `@kebudzis after:<date>` (username handle — some group DMs use this instead)

   b. **Direct message scan** — call `slack_read_channel` for each key contact below,
      passing their user ID as `channel_id` with `oldest=<slack_unix_ts>`.
      Extract any messages asking Kevin to do something or assigning him a task.

      | Person | User ID |
      |--------|---------|
      | Sherwin Zhou | U0BPATG4Z8R |
      | Ravi Vadlamudi | U0AKS2T36LW |
      | Mansi Batra | U09LBNQJY74 |
      | Maqbulhusen Malaiwala | U0BBT2RS2HY |
      | Ravichandra Reddy Palikala | D0B9W0BKA4E |
      | George Palahunik | U0AU6125V1D |
      | Rajesh Khatri | U08QR2YJADV |
      | Sean Walsh | W01239Y3BMG |
      | Victoria Tragianopoulos | W013DHB44G5 |
      | Heather Sands | U01KK5CHWTG |
      | Christina Chiotis | W01435L4K0C |
      | Patrick Shea | U0BQ3EJC6D7 |
      | Vaidehi Patil | U0AKSTX3CBY |
      | Sonam Mishra | U0B5J3P3950 |
      | Vani Gutti | W011KA1BXU6 |
      | Diane Gill | W01239W16FL |
      | Balaji Krishnamurthy | W012FUB9F5X |
      | Aditya Vaidya | U01D630C4N4 |
      | Hemant Shrivastava | U099V32QXKJ |
      | Karen Marx | U09BM5X11AM |
      | Mayuri Panaganti | U08SN2B39DW |
      | Pradheep Krishnaswamy | U09CW45855E |
      | Sukriti Chawla | U0AKYHNDAKE |
      | Awadhesh Pandit | U09706S8TTQ |
      | Arpit Chauhan | U0991KLKMRU |
      | Mukesh Sai Pabbisetty | U09EBQ6P6CC |
      | Ashwin Ayancha | U090ZLH0SRH |
      | Nishant Ashish | U092DSV5FK5 |
      | Shyamkumar Mullaguri | W011YSN5TC1 |
      | Priyanka Megotia | U0BRZDQ7SGZ |
      | Gadangi Mounika | U09K7BEQEUC |
      | Manoj Gadiraju | U0A4XA4TS3S |
      | Somya Ranjan Das | U0BR4V9081J |
      | Srimannarayana Arava | U0AC1GULPFW |
      | Aaditya Sharma | U09GJA95WTF |
      | Andy Kayyali | U09LM7AULLD |
      | Stephen King | U0AL77DG8VA |
      | Slackbot (reminders) | USLACKBOT |
      *(Not found: Philipraja Francis, Upasana Chatterjee, Rob Raman, Zainab Murtaza Bharmal — not in workspace)*
      *(Steven Reeter — D0BBE1Z8BEY — channel_not_found; different Enterprise Grid shard)*

   c. **Group DM scan** — call `slack_read_channel` for each known group DM channel ID below.
      These cannot be read via user ID; use the channel ID directly.

      | Group DM | Channel ID | Members |
      |----------|------------|---------|
      | PoBo and RoBo Walkthrough | C0C0WG488US | George Palahunik, Karen Marx, Prashant Menon, Jeremiah Bolneo, Rob Clements, Sachin Sardana, Jeffany Abellanosa, Joyce Anne Tolentino |
      | Viacom/PPC tracking (Patrick Szeto group) | C0BLFMURLGH | Karen Marx, Patrick Szeto, Brooke Tilton, Victoria Tragianopoulos, Derek Keegan |
      | Patrick Shea + Sherwin Zhou | C0C1UP4TG49 | Patrick Shea, Sherwin Zhou |
      | Isabella Bergloff + Rajesh Khatri | C0C18A824QN | Isabella Bergloff, Rajesh Khatri |
      *(C0BMP7KCJ6B — Mansi/Maqbul/Ravichandra group DM — returns protocol parse error; unreadable via MCP)*

      Convert the ISO cursor timestamp to a Unix timestamp for the `oldest` parameter
      (e.g. `int(datetime.fromisoformat(cursor).timestamp())`).

5. For each source item, extract action items as JSON objects with these keys:
   ```json
   {
     "task":       "Concise imperative task description",
     "workstream": "AR / Integrations | Integrations / LATAM | AR / Billing | AR / Tax | GL / R2R | Testing / Go-Live | General",
     "source":     "Short source label (meeting title + date, or Email: subject + date)",
     "due_date":   "YYYY-MM-DD or empty string",
     "notes":      "Brief context, max 100 chars",
     "status":     "Open"
   }
   ```
   Only include items explicitly assigned to or owned by Kevin.

6. Write all candidates to `C:\GIT\todo_scan_output.json`.

7. Run:
   ```
   python C:\GIT\scan_todos.py --input C:\GIT\todo_scan_output.json
   ```
   The script deduplicates against existing Excel tasks and appends net-new ones.

8. Update the cursor:
   ```
   python C:\GIT\scan_todos.py --update-cursor "tactiq:<ISO_TIMESTAMP>"
   python C:\GIT\scan_todos.py --update-cursor "email:<ISO_TIMESTAMP>"
   python C:\GIT\scan_todos.py --update-cursor "slack:<ISO_TIMESTAMP>"
   ```

9. Report back: how many tasks were found per source, how many were net-new, what they are.

### Key files
- `C:\GIT\scan_todos.py` — Excel comparison + append logic
- `C:\GIT\add_todo.py` — manually add a single task
- `C:\GIT\todo_cursor.json` — last-scan timestamps per source
- `C:\Paramount\local_docs\Kevin_Todo.xlsx` — the tracker itself
- `C:\ParamountRAG\outlook\email\emails.json` — latest exported emails

### Rules
- Close Kevin_Todo.xlsx before writing (Excel locks the file).
- Never ingest content dated before 2026-06-01 without asking.
- Email sync: run `sync_outlook.py --mail` first if emails.json is stale.
