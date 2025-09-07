# Spotify Extended History → Last.fm Scrobbler (Windows-friendly)

A small, privacy-respecting tool that reads **Spotify Extended Streaming History** (`Streaming_History_Audio_*.json`, optionally zipped) and **scrobbles** it to Last.fm.
Works with multiple files at once, supports whole folders and `.zip` archives, deduplicates plays, and sends scrobbles in batches.

* **GUI app (Tkinter)**: simple window to paste your Last.fm API key/secret, pick your ZIP/folder/files, and press *Start*.
* **CLI app**: one command to scrobble all your history (any mix of folders/zip/json paths).

It follows Last.fm’s scrobbling requirements: each scrobble includes artist, track, and the **UNIX start timestamp**; batches of up to **50** scrobbles are sent per request; tracks shorter than **30 s** are ignored (the tool uses the exported `ms_played` to enforce this). These are Last.fm’s published rules and limits for `track.scrobble` and scrobble eligibility.

---

## Features

* **All-at-once ingestion**: select a directory with many files, one or more `.json` files, or one/many `.zip` archives.
* **Spotify data awareness**: understands the Extended History fields (artist/track/album URIs, `ms_played`, device, private mode, etc.).
* **Eligibility & filtering**:

  * ignores podcasts/audiobooks and private sessions,
  * ignores plays with `ms_played < 30 000`,
  * deduplicates identical (artist, track, timestamp) entries.
* **Robust scrobbling**:

  * batches up to **50** scrobbles per request (per Last.fm’s limit), with brief pacing to avoid rate limiting,
  * retries only when recommended by Last.fm (e.g., temporary service errors; asks you to re-authenticate if the session key is invalid).
* **Two ways to use**: GUI or CLI.
* **Windows packaging**: ship a single `.exe` to friends.

---

## Getting your Last.fm API credentials

1. Create an API account (Application name, description, homepage = your GitHub repo URL).
2. **Callback URL** can be left **blank** for a desktop application; interactive approval does not require a web callback (that field is used for web integrations).
3. You will receive an **API key** and **API secret**.

> Keep the **secret** private. Do not commit it to Git or share builds that embed your personal secret.

---

## Quickstart (GUI)

1. Install Python 3.10+ and `requests`:

   ```bash
   pip install requests
   ```
2. Run the GUI:

   ```bash
   python spotify_lastfm_scrobbler_gui.py
   ```
3. Paste your **API key** and **secret** (leave Session Key empty if you don’t have one).
4. Click **Add Files** or **Add Folder** and point to your `Streaming_History_Audio_*.json` and/or `.zip` archives.
5. Click **Start Scrobbling**.
6. On first run, the app opens a Last.fm approval URL. Approve access, return to the window and press **I’ve approved**.
   Your **session key** is stored locally for reuse.

---

## Quickstart (CLI)

```bash
pip install requests
python spotify_lastfm_scrobbler.py \
  --input path\to\history-folder \
          path\to\Streaming_History_Audio_2025_11.json \
          path\to\MoreHistory.zip
```

Useful flags:

* `--dry-run` — show what would be submitted, without sending to Last.fm.
* `--session-key YOUR_LASTFM_SESSION_KEY` — skip interactive approval if you already have a session.

---

## What the tool sends (data mapping)

From Spotify Extended History (per entry):

* `master_metadata_album_artist_name` → **artist**
* `master_metadata_track_name` → **track**
* `master_metadata_album_album_name` → **album** (optional)
* `ts` (UTC end timestamp) and `ms_played` → **start timestamp** (UNIX seconds), computed as `end_ts_utc - floor(ms_played/1000)`

Scrobble eligibility applied:

* Only **music** (not podcasts/audiobooks); private sessions are skipped.
* Only plays with `ms_played ≥ 30 000 ms` (30 s).
* Sent in batches of **≤ 50** scrobbles.

---

## Packaging a Windows `.exe`

1. Install PyInstaller (and requests if you haven’t already):

   ```bash
   pip install pyinstaller requests
   ```
2. Build the GUI app (recommended for friends):

   ```bash
   pyinstaller --onefile --noconsole --name SpotifyHistoryScrobbler spotify_lastfm_scrobbler_gui.py
   ```

   This produces `dist/SpotifyHistoryScrobbler.exe`.
3. (Optional) Build the CLI:

   ```bash
   pyinstaller --onefile --name SpotifyHistoryScrobblerCLI spotify_lastfm_scrobbler.py
   ```

> Ship the `.exe` plus this README. Users only need their own **API key/secret** (or you can give them the app with an empty config so they paste theirs on first run).

---

## Local files & privacy

The app keeps small local JSON files next to the program (or in the working directory):

* `lastfm_credentials.json` — stores your **API key/secret** and **session key** after you approve access.
* `lastfm_session_*.json` — short-lived auth cache during approval flow.
* Optional log files for failures (do not contain your history contents).

**Do not commit your Spotify history to GitHub.** Extended History includes sensitive metadata (e.g., country/IP/device) and should be kept private (see Spotify’s own “Read Me First” in the export).

---

## Error messages you might see (and fixes)

* **Invalid API key / secret** — double-check values.
* **Invalid session key (code 9)** — re-authenticate; the tool will prompt you.
* **Daily scrobble limit exceeded (ignoredMessage code 5)** — wait 24 h.
* **Rate limit exceeded (error 29)** — the app already paces batches; if you hit this, re-run later.
* **Service offline/temporary error (codes 11 or 16)** — retry later; the app only retries when recommended by Last.fm.

---

## .gitignore

Add the following to keep secrets, build artefacts and personal data out of the repo:

```
# Python / env
__pycache__/
*.py[cod]
.venv/ venv/ env/
.env .env.* *.env

# Build / packaging
build/ dist/ *.spec *.exe *.dll

# Editors / OS
.vscode/ .idea/ .DS_Store Thumbs.db

# Logs
*.log

# Local credentials / app cache
lastfm_credentials.json
lastfm_session*.json
config/*.json

# Your history (never commit)
Streaming_History_*.json
*.zip
MyData*/
```

---

## Contributing

* Issues and PRs welcome (please strip personal data from examples).
* Keep changes small and test with `--dry-run` first.

---

## License

MIT (see `LICENSE`).

---

### Summary (actionable)

* Create a Last.fm API key/secret (desktop app; leave **Callback URL** blank).
* Run the **GUI** (`spotify_lastfm_scrobbler_gui.py`) → paste keys → select folder/zip/files → *Start Scrobbling*.
* For friends, build and share the `.exe` with **PyInstaller**.
* Never commit your personal history or secrets.
