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

4. **Slack** — use `slack_search_public_and_private` to search for messages mentioning Kevin
   or assigning him tasks since the cursor timestamp.

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
