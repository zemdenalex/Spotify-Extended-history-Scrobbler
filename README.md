# Spotify → Last.fm Scrobbler (CLI + GUI)

Scrobble your **Spotify Extended Streaming History** to **Last.fm** quickly and safely.

This build includes: robust Last.fm request signing, import-mode timestamp compression (like Scrubbler), duration support, duplicate filtering, **stateful resume** across restarts, and **automatic rate-limit backoff**. A simple GUI is provided to drive the CLI.

---

## Features
- ✅ Reads **Extended Streaming History** (folders/ZIPs/multiple JSONs). Skips video history.
- ✅ Filters out podcasts/audiobooks/private sessions and short plays (<30s).
- ✅ De-dupes by `(artist, track, computed_start_timestamp)` before sending.
- ✅ **Import Mode** to compress scrobbles into a recent window (Scrubbler-style): `--import-mode --finish-at ... --gap-sec ...`.
- ✅ Optional `duration[N]` and `albumArtist[N]` fields for better matching.
- ✅ Optional omission of `chosenByUser[N]` (some imports behave better without it).
- ✅ **State file + resume** to pick up where you left off across restarts.
- ✅ **HTTP 429 backoff** (exponential, respects `Retry-After` if present), with midnight hold if rate-limited aggressively.
- ✅ One-tap **probe** to verify write privileges.
- ✅ Clear debug logging to `scrobble_debug.log` (redacted).

---

## Requirements
- Python 3.10+
- `requests` (`pip install requests`)
- Last.fm API key + secret (from https://www.last.fm/api/account/create)

---

## Files in this repo
- `spotify_lastfm_scrobbler.py` — CLI scrobbler.
- `spotify_lastfm_scrobbler_gui.py` — minimal GUI wrapper for the CLI.

---

## Getting Started

### 1) Install deps
```bash
python -m pip install requests
```

### 2) Export and locate your Spotify data
Use the *Extended Streaming History* export from Spotify. Point the tool at the folder or zip that contains files like `Streaming_History_Audio_*.json`.

### 3) First-time auth
Run a quick write probe to set up a session key (will open a Last.fm Allow dialog):
```bash
python spotify_lastfm_scrobbler.py --probe --debug
```
If you don’t provide `--api-key/--api-secret`, you’ll be prompted once and they’ll be cached alongside the session key in:
```
~/.spotify_lastfm_scrobbler_config.json
```
Use `--auth-reset` to clear cached creds and re-auth.

---

## Typical usage

### Import (compressed timestamps, 1 second apart, ending at now)
```bash
python spotify_lastfm_scrobbler.py \
  --input "C:\\path\\to\\Spotify Extended Streaming History" \
  --since 2021-01-01 \
  --include-duration \
  --no-chosen-by-user \
  --import-mode --finish-at now --gap-sec 1 \
  --state-file "C:\\path\\scrobble_state.json" \
  --debug
```

### Dry run preview (no network calls)
```bash
python spotify_lastfm_scrobbler.py \
  --input "C:\\path\\Spotify Extended Streaming History" \
  --limit 100 \
  --include-duration \
  --no-chosen-by-user \
  --dry-run --debug
```

---

## CLI Flags (reference)

```
--input [paths...]            One or more directories/JSON/ZIPs.
--api-key KEY                 Last.fm API key (if not cached).
--api-secret SECRET           Last.fm API secret (if not cached).
--session-key SK              Provide an existing Last.fm session key.
--dry-run                     Build requests but do not submit.
--debug                       Write detailed debug log (redacted) to scrobble_debug.log.
--limit N                     Max scrobbles to process after filtering.
--since YYYY-MM-DD            Only scrobble on/after this date (UTC).
--until YYYY-MM-DD            Only scrobble before this date (UTC).
--include-duration            Send duration seconds from ms_played.
--no-chosen-by-user           Do not send chosenByUser[N].
--probe                       Send a one-track test scrobble to verify auth.
--auth-reset                  Forget cached session and re-authenticate.

# Import Mode (Scrubbler-like)
--import-mode                 Re-date plays into a tight time window.
--finish-at <ISO|now>         When the last scrobble should appear (e.g. 2025-09-07T00:00:00Z or "now").
--gap-sec N                   Seconds between re-dated scrobbles (default 1).

# Resume + rate limiting
--state-file PATH             Persist progress; resume from last saved offset.
--resume                      Force resume from saved offset (if supported by build).
--max-backoff SECONDS         Cap the exponential backoff delay (optional).
```

> **Note**: If your current build doesn’t show `--resume`/`--max-backoff` in `-h` output, only `--state-file` is required for resuming. The script auto-saves and auto-resumes when `--state-file` is provided.

---

## How it filters & computes timestamps
- **Eligibility**: must have artist & track, not podcast/audiobook, not private/incognito, and `ms_played ≥ 30,000`.
- **Start timestamp**: computed as `end_ts (from Spotify ts) − floor(ms_played/1000)`.
  - If `offline_timestamp` exists and is within ±7 days of the computed start, it is preferred.
- **De-duplication**: duplicates removed by `(artist, track, computed_start_timestamp)` before sending.

---

## Import Mode (avoid duplicates intentionally)
Import Mode **rewrites timestamps** to a new dense range so Last.fm treats them as *new plays*. Example:
```
--import-mode --finish-at 2025-09-07T00:00:00Z --gap-sec 1
```
If you rerun the same import without changing the final window or without resuming, Last.fm will accept them again (because timestamps are new). That’s expected if you want to **re-import**. If you want to **continue** instead of redoing the same chunk, use a **state file**.

### State file = progress marker
When you pass `--state-file path.json`, the scrobbler saves your current offset and timestamp window settings as it runs. On the next run with the same `--state-file`, it will **resume from the last successful batch**, preventing re-sending the same chunk in Import Mode.

---

## Rate limiting (HTTP 429)
- The script backs off **exponentially** and respects `Retry-After` when present.
- If Last.fm keeps 429’ing after several backoffs, the script sleeps until the **next UTC midnight** and continues. With a state file, you can **safely terminate** the process and restart later; it will resume from the saved offset.

**Tips:**
- Use a **1-second `--gap-sec`** in Import Mode; this is standard and helps avoid Last.fm throttling per-play constraints.
- Consider narrowing `--since` to smaller ranges and running multiple sessions over time.

---

## Windows: fire‑and‑forget

### 1) Create a runner CMD file (example)
Save as `run_scrobbler.cmd` somewhere convenient:
```cmd
@echo off
REM Adjust paths and arguments to your environment
SET PY="C:\\Users\\%USERNAME%\\AppData\\Local\\Programs\\Python\\Python313\\python.exe"
SET APPDIR="C:\\Users\\%USERNAME%\\Desktop\\Projects\\000 - Personal\\008 - Spotify Last.fm Scrobbler\\App"
SET STATE="C:\\Users\\%USERNAME%\\Desktop\\scrobble_state.json"

cd %APPDIR%
%PY% spotify_lastfm_scrobbler.py ^
  --input "C:\\Users\\%USERNAME%\\Desktop\\my_spotify_data\\Spotify Extended Streaming History" ^
  --since 2021-01-01 ^
  --include-duration ^
  --no-chosen-by-user ^
  --import-mode --finish-at now --gap-sec 1 ^
  --state-file %STATE% ^
  --debug
```

### 2) Create a Scheduled Task
- Task Scheduler → **Create Task…**
- **General**: Run whether user is logged on or not.
- **Triggers**: At log on (and/or on a schedule).
- **Actions**: Start a program → `run_scrobbler.cmd`.
- **Conditions**: Uncheck *Start the task only if the computer is on AC power* if needed.
- **Settings**: Allow task to be run on demand; If task is already running, then *do not start a new instance*.

Now your scrobble import can survive restarts and will resume automatically because of the **state file**.

---

## GUI Usage (spotify_lastfm_scrobbler_gui.py)
- Choose your **history folder/zip**.
- Set `Since/Until`, `Include Duration`, `No chosenByUser` as needed.
- Enable **Import Mode** and configure `Finish At` and `Gap (sec)`.
- Pick a **State File** path so progress persists and you can resume.
- Click **Start**. The GUI shells the CLI with your options; progress and errors are printed in the log panel.

> The GUI is intentionally minimal: it mirrors the CLI flags and forwards them. For very large imports, the CLI in a normal console window is still the most robust.

---

## Troubleshooting
**429 Too Many Requests**
- Expected during large imports. Let it back off; use `--state-file` to resume after interruptions.

**Everything looks like it’s re-importing**
- In Import Mode, timestamps are *intentionally new*. Use a **state file** to continue from the last offset instead of restarting from the beginning.

**Auth failed or got stuck**
- Run with `--auth-reset` then `--probe` to refresh credentials.

**Debugging**
- Enable `--debug` and check `scrobble_debug.log` for the last request/response (API key + session key are redacted).

**Wrong timestamps**
- If Spotify’s `ts` value is weird, the tool falls back to `offline_timestamp` when close (±7 days). Otherwise, it uses `ts - ms_played`.

---

## Changelog (highlights)
- **v0.9 (this build)**
  - Import Mode timestamp compression.
  - Duration & albumArtist included when available.
  - Optional `chosenByUser` suppression.
  - Stateful resume via `--state-file` and safer duplicate filtering.
  - Exponential backoff for 429s; midnight hold.
  - Stronger debug logging.
- **v0.8**
  - Robust Last.fm signing; shared session and UA; probe & auth reset; date filters; ZIP/dir recursion; duplicate removal.

---

## License
MIT