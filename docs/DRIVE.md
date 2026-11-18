# Google Drive

There are two separate things here: letting **Claude** read your Drive, and getting the
**scraper's output** into Drive.

## 1. Connect Drive to Claude

- **claude.ai / Claude desktop app:** Settings → Connectors → Google Drive → Connect, then sign in
  with the Google account that owns the files and allow access. In a chat, Claude can then search,
  read and (where enabled) create files in that Drive. In this Claude Code session the Google Drive
  connector is already available, so you can ask "find the roster sheet in my Drive" directly.
- **Claude Code reading files as files:** install Google Drive for desktop (below). Your Drive
  then appears as a normal folder on the Mac, and Claude Code can open, read and write it like any
  other folder.

## 2. Put the knowledge base in Drive

1. Install **Google Drive for desktop**: https://www.google.com/drive/download/ and sign in.
   Drive appears at `~/Library/CloudStorage/GoogleDrive-<your email>/My Drive/`.
2. Make an empty folder there, e.g. `My Drive/Opraah/creator_kb`.
3. Either run once:

```bash
.venv/bin/python -m opraah_scraper export --to "~/Library/CloudStorage/GoogleDrive-<your email>/My Drive/Opraah/creator_kb"
```

   or put `OPRAAH_KB_SYNC_DIR=...` in `.env` so every `run` / `youtube` / `export` refreshes it.

The sync copies only changed files and removes files of creators that no longer exist. It refuses
to write into a non-empty folder it didn't create, so it cannot delete your other Drive files.

**Keep `data/opraah.db` out of Drive.** SQLite plus a sync client corrupts databases (the sync
uploads half-written files and can create conflict copies). The database stays local; Drive
gets the generated folders and CSVs, which are rebuilt from it anytime. Back up the database
with `sqlite3 data/opraah.db ".backup ~/backup/opraah-$(date +%F).db"` (safe while running).

Opening CSVs in Google Sheets is fine for reading. Edits made there are not read back: change
`config/brands.csv` or the roster instead.
