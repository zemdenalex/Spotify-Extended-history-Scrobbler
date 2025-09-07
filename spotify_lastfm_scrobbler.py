#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
import time
import urllib.parse
import webbrowser

import requests

LASTFM_API_ROOT = "https://ws.audioscrobbler.com/2.0/"
CONFIG_FILE = Path.home() / ".spotify_lastfm_scrobbler_config.json"

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

def parse_spotify_iso(ts: str) -> dt.datetime:
    """
    Parse Spotify's 'ts' which is 'YYYY-MM-DDTHH:MM:SSZ' (UTC).
    Returns a timezone-aware UTC datetime.
    """
    # Robust parse for '...Z'
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return dt.datetime.fromisoformat(ts).astimezone(dt.timezone.utc)

# ---------------------------
# Last.fm auth flow
# ---------------------------

def build_api_sig(params: Dict[str, str], api_secret: str) -> str:
    """
    Create an API signature by sorting parameters (excluding 'format' and 'callback'),
    concatenating key+value, appending the shared secret, then MD5.
    """
    items = [(k, v) for k, v in params.items() if k not in ("format", "callback")]
    items.sort(key=lambda kv: kv[0])
    sig_str = "".join(k + v for k, v in items) + api_secret
    return md5_hex(sig_str)

def lastfm_post(params: Dict[str, str], api_secret: str, timeout: int = 30) -> requests.Response:
    # Sign
    params["api_sig"] = build_api_sig(params, api_secret)
    return requests.post(LASTFM_API_ROOT, data=params, timeout=timeout)

def request_token(api_key: str, api_secret: str) -> str:
    payload = {
        "method": "auth.getToken",
        "api_key": api_key,
    }
    r = lastfm_post(payload, api_secret)
    r.raise_for_status()
    data = r.json()
    # token can be at data['token'] or data['lfm']['token'] depending on formatter
    token = data.get("token") or data.get("lfm", {}).get("token")
    if not token:
        raise RuntimeError(f"Could not obtain token: {data}")
    return token

def request_session_key(api_key: str, api_secret: str, token: str) -> Tuple[str, str]:
    payload = {
        "method": "auth.getSession",
        "api_key": api_key,
        "token": token,
    }
    r = lastfm_post(payload, api_secret)
    r.raise_for_status()
    data = r.json()
    sess = data.get("session") or data.get("lfm", {}).get("session")
    if not sess:
        raise RuntimeError(f"Could not obtain session: {data}")
    username = sess.get("name")
    session_key = sess.get("key")
    if not username or not session_key:
        raise RuntimeError(f"Incomplete session response: {data}")
    return username, session_key

def authenticate_interactively(api_key: str, api_secret: str) -> str:
    print("Requesting authorization token...")
    token = request_token(api_key, api_secret)
    auth_url = f"https://www.last.fm/api/auth/?api_key={urllib.parse.quote(api_key)}&token={urllib.parse.quote(token)}"
    print("Open this URL and click 'Allow Access':")
    print(auth_url)
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass
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
    Skips *video* history files explicitly.
    """
    import zipfile

    entries: List[Dict[str, Optional[str]]] = []
    for p in paths:
        try:
            if p.is_dir():
                for file in p.rglob("*.json"):
                    # Skip video file
                    if "Streaming_History_Video" in file.name:
                        continue
                    with file.open("r", encoding="utf-8") as fh:
                        data = json.load(fh)
                        if isinstance(data, list):
                            entries.extend(data)
                        else:
                            print(f"Warning: {file} did not contain a list, skipped.")
            elif p.suffix.lower() == ".zip":
                with zipfile.ZipFile(p, "r") as zf:
                    for name in zf.namelist():
                        if not name.lower().endswith(".json"):
                            continue
                        if "Streaming_History_Video" in name:
                            continue
                        try:
                            with zf.open(name) as zfh:
                                raw = zfh.read().decode("utf-8")
                                data = json.loads(raw)
                                if isinstance(data, list):
                                    entries.extend(data)
                                else:
                                    print(f"Warning: {name} in {p.name} is not a list, skipped.")
                        except Exception as exc:
                            print(f"Error reading {name} from {p.name}: {exc}")
            elif p.is_file() and p.suffix.lower() == ".json":
                if "Streaming_History_Video" in p.name:
                    continue
                with p.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if isinstance(data, list):
                        entries.extend(data)
                    else:
                        print(f"Warning: {p.name} did not contain a list, skipped.")
            else:
                print(f"Warning: {p} not found or unsupported, skipped.")
        except Exception as exc:
            print(f"Error reading {p}: {exc}")
    return entries

def is_private(entry: Dict[str, Optional[str]]) -> bool:
    # incognito mode flag varies; check both possible names
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
    # Skip podcasts and audiobooks
    if entry.get("episode_name") or entry.get("episode_show_name"):
        return False
    if entry.get("audiobook_title") or entry.get("audiobook_uri"):
        return False
    # Must have track name and artist
    if not entry.get("master_metadata_track_name") or not entry.get("master_metadata_album_artist_name"):
        return False
    # Play time
    try:
        ms = int(entry.get("ms_played") or 0)
    except Exception:
        ms = 0
    if ms < 30_000:
        return False
    # Private/incognito
    if is_private(entry):
        return False
    return True

def compute_start_timestamp(entry: Dict[str, Optional[str]]) -> int:
    """
    Compute the UNIX start timestamp (UTC seconds).
    Primary source: 'ts' (UTC end) minus floor(ms_played/1000).
    If 'offline_timestamp' exists and looks sane, prefer it, as Spotify records
    true play times while offline.
    """
    ms_played = int(entry.get("ms_played") or 0)
    # 1) Base on ts
    ts_raw = entry.get("ts")
    if not ts_raw:
        # Fallback: if nothing else, use 'offline_timestamp' directly
        off = entry.get("offline_timestamp")
        if off:
            try:
                off_sec = int(off)
                return off_sec
            except Exception:
                return int(time.time())
        return int(time.time())

    try:
        end_dt = parse_spotify_iso(ts_raw)  # aware UTC
    except Exception:
        # extreme fallback: current time
        end_dt = dt.datetime.now(dt.timezone.utc)

    start_dt = end_dt - dt.timedelta(seconds=ms_played // 1000)

    # 2) If offline_timestamp looks like a sane UTC seconds value (± 7 days of start_dt),
    # prefer it (this preserves original play time for offline plays).
    off = entry.get("offline_timestamp")
    if off:
        try:
            off_sec = int(off)
            off_dt = dt.datetime.fromtimestamp(off_sec, tz=dt.timezone.utc)
            # sanity: if offline time is within a week of the computed start time, accept it
            if abs((off_dt - start_dt).total_seconds()) <= 7 * 24 * 3600:
                start_dt = off_dt
        except Exception:
            pass

    return int(start_dt.timestamp())

# ---------------------------
# Scrobbling
# ---------------------------

def build_scrobble_params(batch: List[Dict[str, Optional[str]]],
                          api_key: str,
                          api_secret: str,
                          session_key: str) -> Dict[str, str]:
    """
    Build a signed parameter dict for track.scrobble with up to 50 items.
    """
    params: Dict[str, str] = {
        "method": "track.scrobble",
        "api_key": api_key,
        "sk": session_key,
    }
    for i, e in enumerate(batch):
        artist = e.get("master_metadata_album_artist_name") or ""
        track = e.get("master_metadata_track_name") or ""
        album = e.get("master_metadata_album_album_name") or ""
        ts = str(compute_start_timestamp(e))
        params[f"artist[{i}]"] = artist
        params[f"track[{i}]"] = track
        if album:
            params[f"album[{i}]"] = album
        params[f"timestamp[{i}]"] = ts
        # Optional: help the service disambiguate bulk imports
        params[f"chosenByUser[{i}]"] = "1"
    # signature will be added by lastfm_post
    return params

def submit_batch(batch: List[Dict[str, Optional[str]]],
                 api_key: str,
                 api_secret: str,
                 session_key: str,
                 dry_run: bool = False,
                 debug: bool = False) -> bool:
    """
    Submit a batch of scrobbles to Last.fm.
    Returns True if accepted>0 or 'ok' status; False otherwise.
    """
    params = build_scrobble_params(batch, api_key, api_secret, session_key)
    params["format"] = "json"

    if dry_run:
        print("Dry-run: would submit the following scrobbles:")
        for i in range(len(batch)):
            print(f"  {params[f'artist[{i}]']} – {params[f'track[{i}]']} at {params[f'timestamp[{i}]']}")
        # NEW: write params to debug log when --debug is on
        if debug:
            redacted = {k: v for k, v in params.items() if not k.startswith(("api_secret", "sk"))}
            with open("scrobble_debug.log", "a", encoding="utf-8") as lf:
                lf.write("[DRY_RUN] REQUEST PARAMS (redacted): " + json.dumps(redacted, ensure_ascii=False) + "\n")
        return True


    # Optional debug logging
    def _log_debug(message: str) -> None:
        if not debug:
            return
        with open("scrobble_debug.log", "a", encoding="utf-8") as lf:
            lf.write(message + "\n")

    try:
        # Redact secret/sk in logs
        if debug:
            redacted = {k: v for k, v in params.items() if not k.startswith(("api_secret", "sk"))}
            _log_debug("REQUEST PARAMS (redacted): " + json.dumps(redacted, ensure_ascii=False)[:4000])

        resp = lastfm_post(params, api_secret)
        text = resp.text
        if debug:
            _log_debug("RESPONSE TEXT: " + text[:8000])

        resp.raise_for_status()
        data = resp.json()

        # Newer responses: data['scrobbles']['@attr']['accepted'] & ['ignored']
        scrobbles = data.get("scrobbles") or data.get("lfm", {}).get("scrobbles")
        if isinstance(scrobbles, dict):
            attr = scrobbles.get("@attr") or {}
            accepted = int(attr.get("accepted") or 0)
            ignored = int(attr.get("ignored") or 0)
            if accepted > 0:
                return True
            # Log first ignored reason if present
            sc = scrobbles.get("scrobble")
            items = sc if isinstance(sc, list) else ([sc] if sc else [])
            if items:
                ig = items[0].get("ignoredMessage") if isinstance(items[0], dict) else None
                print(f"Warning: batch ignored: accepted={accepted}, ignored={ignored}, first reason={ig}")
            else:
                print(f"Warning: batch ignored: accepted={accepted}, ignored={ignored}")
            return False

        # Fallback older style
        status = data.get("lfm", {}).get("status")
        if status == "ok":
            return True

        print(f"Warning: scrobble batch may have failed: {data}")
        return False

    except Exception as exc:
        print(f"Error submitting scrobbles: {exc}")
        return False

# ---------------------------
# Main
# ---------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrobble Spotify extended streaming history to Last.fm.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", nargs="+", required=True,
                        help="One or more directories, JSON files, or ZIP archives (any mix).")
    parser.add_argument("--api-key", help="Your Last.fm API key. If omitted, uses stored key or prompts.")
    parser.add_argument("--api-secret", help="Your Last.fm API secret. If omitted, uses stored secret or prompts.")
    parser.add_argument("--session-key", help="Existing Last.fm session key. Skip interactive auth if provided.")
    parser.add_argument("--dry-run", action="store_true", help="Build requests but do not submit them.")
    parser.add_argument("--debug", action="store_true",
                        help="Write request/response debug info to scrobble_debug.log (secrets redacted).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max number of scrobbles to process (useful for small test submissions).")

    args = parser.parse_args()

    # Resolve inputs
    paths: List[Path] = []
    for inp in args.input:
        p = Path(inp).expanduser()
        if not p.exists():
            print(f"Warning: {inp} does not exist, skipping")
            continue
        paths.append(p)
    if not paths:
        print("Error: no valid input files provided.")
        raise SystemExit(1)

    config = load_config()
    api_key = args.api_key or config.get("api_key")
    api_secret = args.api_secret or config.get("api_secret")
    session_key = args.session_key or config.get("session_key")

    if not api_key:
        api_key = input("Enter your Last.fm API key: ").strip()
    if not api_secret:
        api_secret = input("Enter your Last.fm API secret: ").strip()

    if not session_key:
        try:
            session_key = authenticate_interactively(api_key, api_secret)
            config.update({"api_key": api_key, "api_secret": api_secret, "session_key": session_key})
            save_config(config)
        except Exception as exc:
            print(f"Authentication failed: {exc}")
            raise SystemExit(1)

    # Load entries
    print(f"Reading inputs ({len(paths)} item[s])...")
    entries = parse_streaming_history(paths)
    print(f"Loaded {len(entries)} total entries.")
    scrobble_candidates = [e for e in entries if should_scrobble(e)]
    print(f"{len(scrobble_candidates)} entries qualify for scrobbling.")

    if not scrobble_candidates:
        print("Nothing to scrobble. Exiting.")
        return

    # De-duplicate by (artist, track, start_ts)
    unique: List[Dict[str, Optional[str]]] = []
    seen: set = set()
    for e in scrobble_candidates:
        ts = compute_start_timestamp(e)
        key = (e.get("master_metadata_album_artist_name"), e.get("master_metadata_track_name"), ts)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    print(f"{len(unique)} unique scrobbles after removing duplicates.")

    # Sort by timestamp ascending
    unique.sort(key=lambda e: compute_start_timestamp(e))

    # NEW: optional limit
    if args.limit is not None:
        unique = unique[:max(0, args.limit)]

    # Submit in batches
    total = len(unique)
    batch_size = 50
    submitted = 0
    for i in range(0, total, batch_size):
        batch = unique[i:i+batch_size]
        print(f"Submitting scrobbles {i+1}–{i+len(batch)} of {total}...")
        ok = submit_batch(batch, api_key, api_secret, session_key, dry_run=args.dry_run, debug=args.debug)
        if ok:
            submitted += len(batch)
        # gentle pacing
        if not args.dry_run:
            time.sleep(0.5)

    print(f"Finished. {submitted} scrobbles " + ("processed (dry-run)." if args.dry_run else "submitted."))


if __name__ == "__main__":
    main()
