# --- FILE: spotify_lastfm_scrobbler.py ---
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spotify → Last.fm Scrobbler (CLI) — v0.9

Highlights in this patched build:
- Robust Last.fm signing (ASCII key sort; excludes 'format'/'callback').
- Identified client User-Agent and shared requests.Session.
- Optional omission of chosenByUser (--no-chosen-by-user).
- Optional inclusion of duration from ms_played (--include-duration).
- Date range filters (--since, --until; YYYY-MM-DD).
- One-tap write test (--probe) and auth reset (--auth-reset).
- Stronger debug logging (request+response, redacted) to scrobble_debug.log.
- Safer timestamp computation (handles str/int/datetime 'ts'; prefers offline_timestamp when sane).
- ZIP/dir recursion; skips video history; de-dup by (artist, track, ts).
- Import mode: re-date plays to recent times (like Scrubbler’s ImportMode).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union
import time
import urllib.parse
import webbrowser
import zipfile

import requests

LASTFM_API_ROOT = "https://ws.audioscrobbler.com/2.0/"
CONFIG_FILE = Path.home() / ".spotify_lastfm_scrobbler_config.json"
USER_AGENT = "Spotify-Extended-History-Scrobbler/0.9 (+https://github.com/zemdenalex/Spotify-Extended-history-Scrobbler)"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

SIGNING_SKIP = {"format", "callback"}
DEBUG_LOG = Path("scrobble_debug.log")

# ---------------------------
# Utilities
# ---------------------------

def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def load_config() -> Dict[str, str]:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_config(cfg: Dict[str, str]) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def delete_config() -> None:
    try:
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()
    except Exception:
        pass

def log_debug(line: str) -> None:
    try:
        DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOG.open("a", encoding="utf-8") as lf:
            lf.write(line + "\n")
    except Exception:
        pass

def parse_spotify_iso(ts: str) -> dt.datetime:
    """
    Parse Spotify 'ts' string 'YYYY-MM-DDTHH:MM:SSZ' (UTC) to an aware UTC datetime.
    """
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return dt.datetime.fromisoformat(ts).astimezone(dt.timezone.utc)

# ---------------------------
# Last.fm auth flow
# ---------------------------

def build_api_sig(params: Dict[str, str], api_secret: str) -> str:
    """
    Sort parameters (excluding 'format'/'callback') by ASCII key, concatenate key+value,
    append secret, MD5.
    """
    items = [(k, v) for k, v in params.items() if k not in SIGNING_SKIP]
    items.sort(key=lambda kv: kv[0])
    sig_str = "".join(k + v for k, v in items) + api_secret
    return md5_hex(sig_str)

def lastfm_post(params: Dict[str, str], api_secret: str, timeout: int = 30) -> requests.Response:
    params = dict(params)
    params["api_sig"] = build_api_sig(params, api_secret)
    return SESSION.post(LASTFM_API_ROOT, data=params, timeout=timeout)

def request_token(api_key: str, api_secret: str) -> str:
    payload = {"method": "auth.getToken", "api_key": api_key}
    r = lastfm_post(payload, api_secret); r.raise_for_status()
    data = r.json()
    return data.get("token") or data.get("lfm", {}).get("token") or (_ for _ in ()).throw(RuntimeError(f"Could not obtain token: {data}"))

def request_session_key(api_key: str, api_secret: str, token: str) -> Tuple[str, str]:
    payload = {"method": "auth.getSession", "api_key": api_key, "token": token}
    r = lastfm_post(payload, api_secret); r.raise_for_status()
    data = r.json()
    sess = data.get("session") or data.get("lfm", {}).get("session")
    if not sess:
        raise RuntimeError(f"Could not obtain session: {data}")
    username, session_key = sess.get("name"), sess.get("key")
    if not username or not session_key:
        raise RuntimeError(f"Incomplete session response: {data}")
    return username, session_key

def authenticate_interactively(api_key: str, api_secret: str) -> str:
    print("Requesting authorization token...")
    token = request_token(api_key, api_secret)
    auth_url = f"https://www.last.fm/api/auth/?api_key={urllib.parse.quote(api_key)}&token={urllib.parse.quote(token)}"
    print("Open this URL and click 'Allow Access':"); print(auth_url)
    try: webbrowser.open(auth_url)
    except Exception: pass
    input("After approving, press Enter to continue...")
    print("Exchanging token for session key...")
    username, session_key = request_session_key(api_key, api_secret, token)
    print(f"Authenticated as {username}.")
    return session_key

# ---------------------------
# Spotify history parsing
# ---------------------------

def parse_streaming_history(paths: Iterable[Path]) -> List[Dict[str, Optional[str]]]:
    """
    Load and flatten multiple streaming history JSON files.
    Accepts directories, .json files, or .zip archives. Recurses into directories.
    Skips video history files explicitly.
    """
    entries: List[Dict[str, Optional[str]]] = []
    for p in paths:
        try:
            if p.is_dir():
                for file in p.rglob("*.json"):
                    if "Streaming_History_Video" in file.name:
                        continue
                    with file.open("r", encoding="utf-8") as fh:
                        data = json.load(fh)
                        if isinstance(data, list):
                            entries.extend(data)
                for file in p.rglob("*.zip"):
                    entries.extend(_read_zip_items(file))
            elif p.suffix.lower() == ".zip":
                entries.extend(_read_zip_items(p))
            elif p.is_file() and p.suffix.lower() == ".json":
                if "Streaming_History_Video" in p.name:
                    continue
                with p.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if isinstance(data, list):
                        entries.extend(data)
            else:
                print(f"Warning: {p} not found or unsupported, skipped.")
        except Exception as exc:
            print(f"Error reading {p}: {exc}")
    return entries

def _read_zip_items(zippath: Path) -> List[Dict[str, Optional[str]]]:
    out: List[Dict[str, Optional[str]]] = []
    try:
        with zipfile.ZipFile(zippath, "r") as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".json"): continue
                if "Streaming_History_Video" in name: continue
                try:
                    with zf.open(name) as zfh:
                        raw = zfh.read().decode("utf-8")
                        data = json.loads(raw)
                        if isinstance(data, list):
                            out.extend(data)
                except Exception as exc:
                    print(f"Error reading {name} from {zippath.name}: {exc}")
    except Exception as exc:
        print(f"Error opening zip {zippath}: {exc}")
    return out

def is_private(entry: Dict[str, Optional[str]]) -> bool:
    v = entry.get("incognito_mode")
    if v is None:
        v = entry.get("is_private_session")
    return bool(v)

def should_scrobble(entry: Dict[str, Optional[str]]) -> bool:
    """
    Conditions:
    - track + artist must exist
    - ms_played >= 30_000
    - not a podcast/audiobook
    - not private/incognito session
    """
    if entry.get("episode_name") or entry.get("episode_show_name"):
        return False
    if entry.get("audiobook_title") or entry.get("audiobook_uri"):
        return False
    if not entry.get("master_metadata_track_name") or not entry.get("master_metadata_album_artist_name"):
        return False
    try:
        ms = int(entry.get("ms_played") or 0)
    except Exception:
        ms = 0
    if ms < 30_000:
        return False
    if is_private(entry):
        return False
    return True

def compute_start_timestamp(entry: Dict[str, Optional[str]]) -> int:
    """
    Compute the UNIX start timestamp (UTC seconds).
    Based on 'ts' (UTC end) minus floor(ms_played/1000).
    Prefer 'offline_timestamp' when it is close (±7 days) to the computed start.
    Accepts 'ts' as str, int/float (epoch seconds), or datetime.
    """
    ms_played = int(entry.get("ms_played") or 0)

    ts_raw: Union[str, int, float, dt.datetime, None] = entry.get("ts")  # may be absent
    start_dt: dt.datetime

    if isinstance(ts_raw, (int, float)):
        end_dt = dt.datetime.fromtimestamp(int(ts_raw), tz=dt.timezone.utc)
        start_dt = end_dt - dt.timedelta(seconds=ms_played // 1000)
    elif isinstance(ts_raw, dt.datetime):
        end_dt = ts_raw.astimezone(dt.timezone.utc)
        start_dt = end_dt - dt.timedelta(seconds=ms_played // 1000)
    elif isinstance(ts_raw, str):
        try:
            end_dt = parse_spotify_iso(ts_raw)
        except Exception:
            end_dt = dt.datetime.now(dt.timezone.utc)
        start_dt = end_dt - dt.timedelta(seconds=ms_played // 1000)
    else:
        start_dt = dt.datetime.now(dt.timezone.utc)

    off = entry.get("offline_timestamp")
    if off:
        try:
            off_sec = int(off)
            off_dt = dt.datetime.fromtimestamp(off_sec, tz=dt.timezone.utc)
            if abs((off_dt - start_dt).total_seconds()) <= 7 * 24 * 3600:
                start_dt = off_dt
        except Exception:
            pass

    return int(start_dt.timestamp())

# ---------------------------
# ImportMode helpers
# ---------------------------

def parse_finish_at(s: str | None) -> int:
    if not s or s.lower() == "now":
        return int(dt.datetime.now(dt.timezone.utc).timestamp())
    if "T" in s:
        d = dt.datetime.fromisoformat(s)
    else:
        d = dt.datetime.fromisoformat(s + "T00:00:00")
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.astimezone(dt.timezone.utc).timestamp())

def apply_import_mode(scrobbles: list[dict], finish_at: str | None, gap_sec: int | None) -> None:
    """
    Rewrite timestamps in-place so that items are evenly spaced and end at finish_at.
    Stores '_ts_override' on each item; build_scrobble_params will prefer it.
    """
    gap = max(1, int(gap_sec or 1))
    finish_ts = parse_finish_at(finish_at)
    n = len(scrobbles)
    start_ts = finish_ts - (n - 1) * gap
    for i, e in enumerate(scrobbles):
        e["_ts_override"] = start_ts + i

# ---------------------------
# Scrobbling
# ---------------------------

def build_scrobble_params(
    batch: List[Dict[str, Optional[str]]],
    api_key: str,
    api_secret: str,
    session_key: str,
    include_duration: bool = False,
    send_chosen: bool = True,
) -> Dict[str, str]:
    """Build parameter dict for track.scrobble (<=50 items)."""
    params: Dict[str, str] = {
        "method": "track.scrobble",
        "api_key": api_key,
        "sk": session_key,
        "format": "json",
    }
    for i, e in enumerate(batch):
        artist = e.get("master_metadata_album_artist_name") or ""
        track = e.get("master_metadata_track_name") or ""
        album = e.get("master_metadata_album_album_name") or ""
        override = e.get("_ts_override") or e.get("timestamp")
        ts = str(int(override)) if override is not None else str(compute_start_timestamp(e))
        params[f"artist[{i}]"] = artist
        params[f"track[{i}]"] = track
        params[f"timestamp[{i}]"] = ts
        if album:
            params[f"album[{i}]"] = album
            params[f"albumArtist[{i}]"] = artist  # assist matcher
        if send_chosen:
            params[f"chosenByUser[{i}]"] = "1"
        if include_duration:
            try:
                dur = int((int(e.get("ms_played") or 0)) // 1000)
                if dur > 0:
                    params[f"duration[{i}]"] = str(dur)
            except Exception:
                pass
    return params

def _redacted(params: Dict[str, str]) -> Dict[str, str]:
    return {k: v for k, v in params.items() if k.lower() not in {"api_sig", "sk"}}

def submit_batch(
    batch: List[Dict[str, Optional[str]]],
    api_key: str,
    api_secret: str,
    session_key: str,
    *,
    dry_run: bool = False,
    debug: bool = False,
    include_duration: bool = False,
    send_chosen: bool = True,
) -> Dict[str, int]:
    """Submit a batch; returns dict with accepted/ignored."""
    params = build_scrobble_params(batch, api_key, api_secret, session_key, include_duration, send_chosen)

    if dry_run:
        if debug:
            log_debug("[DRY_RUN] REQUEST PARAMS (redacted): " + json.dumps(_redacted(params), ensure_ascii=False))
        return {"accepted": 0, "ignored": len(batch)}

    if debug:
        log_debug("REQUEST PARAMS (redacted): " + json.dumps(_redacted(params), ensure_ascii=False)[:6000])

    resp = lastfm_post(params, api_secret)
    text = resp.text
    if debug:
        log_debug("RESPONSE TEXT: " + text[:12000])
    resp.raise_for_status()
    data = resp.json()

    scrob = data.get("scrobbles") or data.get("lfm", {}).get("scrobbles")
    if isinstance(scrob, dict):
        attr = scrob.get("@attr") or {}
        accepted = int(attr.get("accepted") or 0)
        ignored = int(attr.get("ignored") or 0)
        if accepted == 0:
            payload = scrob.get("scrobble")
            items = payload if isinstance(payload, list) else ([payload] if payload else [])
            reason = None
            for it in items:
                msg = (it or {}).get("ignoredMessage", {})
                if msg.get("code") and msg.get("code") != "0":
                    reason = {"code": msg.get("code"), "text": msg.get("#text", "")}
                    break
            print(f"Warning: batch ignored: accepted={accepted}, ignored={ignored}, first reason={reason}")
        return {"accepted": accepted, "ignored": ignored}

    status = data.get("lfm", {}).get("status")
    if status == "ok":
        return {"accepted": len(batch), "ignored": 0}

    print(f"Warning: unexpected response: {data}")
    return {"accepted": 0, "ignored": len(batch)}

# ---------------------------
# CLI
# ---------------------------

def within_range(ts_sec: int, since_str: Optional[str], until_str: Optional[str]) -> bool:
    if since_str:
        try:
            since = dt.datetime.fromisoformat(since_str).replace(tzinfo=dt.timezone.utc).timestamp()
            if ts_sec < int(since):
                return False
        except Exception:
            pass
    if until_str:
        try:
            until = dt.datetime.fromisoformat(until_str).replace(tzinfo=dt.timezone.utc).timestamp()
            if ts_sec >= int(until):
                return False
        except Exception:
            pass
    return True

def run_probe(api_key: str, api_secret: str, session_key: Optional[str], debug: bool = False) -> None:
    if not session_key:
        session_key = authenticate_interactively(api_key, api_secret)
        cfg = load_config(); cfg.update({"api_key": api_key, "api_secret": api_secret, "session_key": session_key}); save_config(cfg)

    now = int(time.time()) - 120
    probe_entry = {
        "master_metadata_album_artist_name": "Nirvana",
        "master_metadata_track_name": "Smells Like Teen Spirit",
        "master_metadata_album_album_name": "Nevermind",
        "ms_played": 300000,
        "timestamp": now,  # explicit override so no parsing is needed
    }
    res = submit_batch([probe_entry], api_key, api_secret, session_key, dry_run=False, debug=debug, include_duration=True, send_chosen=False)
    print(f"[probe] accepted={res['accepted']} ignored={res['ignored']}")

def main() -> None:
    p = argparse.ArgumentParser(
        description="Scrobble Spotify extended streaming history to Last.fm.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--input", nargs="+", help="One or more directories/JSON/ZIPs.")
    p.add_argument("--api-key", help="Last.fm API key")
    p.add_argument("--api-secret", help="Last.fm API secret")
    p.add_argument("--session-key", help="Existing Last.fm session key")
    p.add_argument("--dry-run", action="store_true", help="Build requests but do not submit")
    p.add_argument("--debug", action="store_true", help="Write scrobble_debug.log (redacted)")
    p.add_argument("--limit", type=int, default=None, help="Max scrobbles to process after filtering")
    p.add_argument("--since", type=str, help="Only scrobble on/after this date (YYYY-MM-DD)")
    p.add_argument("--until", type=str, help="Only scrobble before this date (YYYY-MM-DD)")
    p.add_argument("--include-duration", action="store_true", help="Send duration seconds from ms_played")
    p.add_argument("--no-chosen-by-user", action="store_true", help="Do not send chosenByUser[N]")
    p.add_argument("--probe", action="store_true", help="Send a one-track test scrobble (verifies write auth)")
    p.add_argument("--auth-reset", action="store_true", help="Forget cached session and re-auth")
    p.add_argument("--import-mode", action="store_true", help="Re-date plays to recent times for Last.fm import (like Scrubbler).")
    p.add_argument("--finish-at", default="now", help='When the last scrobble should appear (ISO datetime or "now").')
    p.add_argument("--gap-sec", type=int, default=1, help="Seconds between re-dated scrobbles in import mode (default: 1).")

    args = p.parse_args()

    if args.auth_reset:
        delete_config()
        print("Cleared cached credentials.")

    cfg = load_config()
    api_key = args.api_key or cfg.get("api_key") or input("Enter your Last.fm API key: ").strip()
    api_secret = args.api_secret or cfg.get("api_secret") or input("Enter your Last.fm API secret: ").strip()
    session_key = args.session_key or cfg.get("session_key")

    if args.probe:
        run_probe(api_key, api_secret, session_key, debug=args.debug)
        return

    inputs: List[Path] = []
    if not args.input:
        print("Error: --input is required (or use --probe)")
        raise SystemExit(2)
    for s in args.input:
        pth = Path(s).expanduser()
        if not pth.exists():
            print(f"Warning: {s} not found; skipped")
            continue
        inputs.append(pth)
    if not inputs:
        print("Error: no valid inputs")
        raise SystemExit(2)

    if not session_key:
        try:
            session_key = authenticate_interactively(api_key, api_secret)
            cfg.update({"api_key": api_key, "api_secret": api_secret, "session_key": session_key})
            save_config(cfg)
        except Exception as exc:
            print(f"Authentication failed: {exc}")
            raise SystemExit(1)

    print(f"Reading inputs ({len(inputs)} item[s])...")
    entries = parse_streaming_history(inputs)
    print(f"Loaded {len(entries)} total entries.")

    candidates = [e for e in entries if should_scrobble(e)]
    print(f"{len(candidates)} entries qualify for scrobbling.")

    # De-duplicate and apply date range
    seen = set()
    unique: List[Dict[str, Optional[str]]] = []
    for e in candidates:
        ts = compute_start_timestamp(e)
        if not within_range(ts, args.since, args.until):
            continue
        key = (e.get("master_metadata_album_artist_name"), e.get("master_metadata_track_name"), ts)
        if key not in seen:
            seen.add(key); unique.append(e)

    if not unique:
        print("Nothing to scrobble after filtering.")
        return

    unique.sort(key=lambda e: compute_start_timestamp(e))
    print(f"{len(unique)} unique scrobbles after removing duplicates.")

    if args.limit is not None:
        unique = unique[: max(0, args.limit)]

    if args.import_mode:
        apply_import_mode(unique, args.finish_at, args.gap_sec)
        if args.debug and unique:
            preview = [unique[i].get("_ts_override") for i in range(min(3, len(unique)))]
            tail = [unique[-i].get("_ts_override") for i in range(min(3, len(unique)), 0, -1)]
            log_debug(f"[IMPORT_MODE] first/last overrides: {preview} ... {tail}")

    total = len(unique)
    submitted = 0

    for i in range(0, total, 50):
        batch = unique[i : i + 50]
        print(f"Submitting scrobbles {i+1}–{i+len(batch)} of {total}...")
        res = submit_batch(
            batch,
            api_key,
            api_secret,
            session_key,
            dry_run=args.dry_run,
            debug=args.debug,
            include_duration=args.include_duration,
            send_chosen=(not args.no_chosen_by_user),
        )
        if res.get("accepted", 0) > 0:
            submitted += min(50, len(batch))
        time.sleep(0.5 if not args.dry_run else 0)

    print("Finished. " + (f"{submitted} scrobbles processed (dry-run)." if args.dry_run else f"{submitted} scrobbles submitted."))

if __name__ == "__main__":
    main()
