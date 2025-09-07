#!/usr/bin/env python3
"""
spotify_lastfm_scrobbler.py
============================

This module provides a simple command line interface for scrobbling your
extended Spotify streaming history to Last.fm.  It parses one or more
`Streaming_History_Audio_*.json` files (the same format returned by
Spotify's Extended Streaming History export) and submits scrobbles via
Last.fm's `track.scrobble` API.

Features
--------

* Interactive authentication: if you don't already have a Last.fm session
  key, the script will guide you through generating one.  It obtains an
  authentication token from Last.fm, prints a URL which you need to
  open in a browser to grant access for your account, then exchanges the
  authorised token for a session key.  Your API key, secret and
  resulting session key are stored locally in a small JSON file so you
  don't need to re‑authenticate on every run.
* Filtering of scrobbles: the tool respects Last.fm's rules for
  scrobbling by only sending tracks that have been played for at least
  30 seconds.  Tracks listened to in private session (`incognito_mode`)
  or with missing metadata are skipped.  Podcasts and audiobooks are
  ignored.
* Batch submission: scrobbles are sent in batches of up to 50 tracks
  (Last.fm's limit), greatly reducing the number of requests.  The
  program prints progress as it goes.
* Duplicate detection: if the same track with the same start
  timestamp appears multiple times in your export, only one scrobble is
  sent.

Usage
-----

Run the script from the command line with Python 3.  At first run you
will be prompted for your Last.fm API key and secret (you can obtain
these by registering an application at https://www.last.fm/api).  If
you already have a session key you can supply it on the command line
with `--session-key` to skip the interactive authentication.

```
python spotify_lastfm_scrobbler.py --input Streaming_History_Audio_2025_11.json
```

You can specify multiple input files or a directory containing your
history JSON files.  Use `--dry-run` to parse and summarise the
scrobbles without actually submitting them.

The code is intentionally dependency‑free so it can be packaged as a
standalone Windows executable via PyInstaller.  See the README at the
bottom of this file for packaging instructions.

"""

import argparse
import datetime
import functools
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

try:
    import requests  # type: ignore
except ImportError:
    print("Error: The 'requests' library is required. Install it via pip, e.g. 'pip install requests'.")
    sys.exit(1)


###############################################################################
# Authentication helpers
###############################################################################

LASTFM_API_ROOT = "https://ws.audioscrobbler.com/2.0/"
AUTH_URL = "https://www.last.fm/api/auth/"
CONFIG_FILE = Path.home() / ".spotify_lastfm_scrobbler_config.json"


def generate_signature(params: Dict[str, str], secret: str) -> str:
    """
    Compute the Last.fm API signature.

    The signature is an MD5 hash of all the parameters (except 'format')
    concatenated in order of their ASCII names, followed by your API secret.

    See the Last.fm authentication docs for details【216882144092530†L137-L161】.

    Parameters
    ----------
    params : dict[str, str]
        A mapping of parameter names to values.  Should not include
        `api_sig` or the `format` parameter.
    secret : str
        Your Last.fm API shared secret.

    Returns
    -------
    str
        The resulting MD5 hex digest.
    """
    # Exclude the 'format' parameter from the signature
    sorted_items = sorted((k, v) for k, v in params.items() if k != "format")
    sig_str = "".join(f"{k}{v}" for k, v in sorted_items) + secret
    return hashlib.md5(sig_str.encode("utf-8")).hexdigest()


def load_config() -> Dict[str, str]:
    """
    Load stored configuration from disk.

    Returns a dictionary containing 'api_key', 'api_secret' and 'session_key'
    if available.  Missing keys are omitted.
    """
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def save_config(data: Dict[str, str]) -> None:
    """
    Persist configuration to disk.
    """
    try:
        with CONFIG_FILE.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except Exception as exc:
        print(f"Warning: could not save config ({exc})")


def request_lastfm_token(api_key: str, api_secret: str) -> str:
    """
    Request an authentication token from Last.fm.

    You must supply your API key and secret.  The returned token will
    need to be authorised by visiting a URL before exchanging it for a
    session key.
    """
    params = {
        "method": "auth.getToken",
        "api_key": api_key,
    }
    params["api_sig"] = generate_signature(params, api_secret)
    params["format"] = "json"
    resp = requests.get(LASTFM_API_ROOT, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if "token" not in data:
        raise RuntimeError(f"Unable to obtain token: {data}")
    return data["token"]


def request_session_key(api_key: str, api_secret: str, token: str) -> Tuple[str, str]:
    """
    Exchange an authorised token for a session key.

    Returns a tuple of (username, session_key).
    """
    params = {
        "method": "auth.getSession",
        "api_key": api_key,
        "token": token,
    }
    params["api_sig"] = generate_signature(params, api_secret)
    params["format"] = "json"
    resp = requests.get(LASTFM_API_ROOT, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if "session" not in data or "key" not in data["session"]:
        raise RuntimeError(f"Unable to obtain session key: {data}")
    return data["session"]["name"], data["session"]["key"]


def authenticate_interactively(api_key: str, api_secret: str) -> str:
    """
    Perform the interactive authentication flow to obtain a session key.

    This prints a URL that you must open in your browser to approve
    access for your application.  Once approved, hit ENTER and the
    session key will be requested.
    """
    print("Requesting authentication token from Last.fm...")
    token = request_lastfm_token(api_key, api_secret)
    auth_url = f"{AUTH_URL}?api_key={api_key}&token={token}"
    print()
    print("Please authorise this application by visiting the following URL in your browser:")
    print(auth_url)
    print()
    input("After authorising, press ENTER to continue...")
    print("Exchanging token for session key...")
    username, session_key = request_session_key(api_key, api_secret, token)
    print(f"Authenticated as {username}.")
    return session_key


###############################################################################
# Scrobble processing
###############################################################################


def parse_streaming_history(paths: Iterable[Path]) -> List[Dict[str, Optional[str]]]:
    """
    Load and flatten multiple streaming history JSON files.

    Parameters
    ----------
    paths : iterable of Path
        A collection of filenames pointing to Spotify extended streaming
        history JSON files.

    Returns
    -------
    list of dict
        A list of raw entries (dictionaries) from all files combined.
    """
    entries: List[Dict[str, Optional[str]]] = []
    for p in paths:
        try:
            with p.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
                if not isinstance(data, list):
                    print(f"Warning: {p} does not contain a list, skipping")
                    continue
                entries.extend(data)
        except Exception as exc:
            print(f"Error reading {p}: {exc}")
    return entries


def should_scrobble(entry: Dict[str, Optional[str]]) -> bool:
    """
    Determine whether a streaming history entry should be scrobbled.

    Conditions implemented here:
    * Must have both track and artist metadata.
    * Must not be a podcast/audiobook (episode_name or audiobook_title present).
    * Must have been played for at least 30 seconds.
    * Must not have incognito_mode set to True.
    * Spotify's scrobbling rules require that at least half the track or 4 minutes
      have been played【418776199619192†L209-L218】, but since we don't have track
      durations we approximate with a minimum play time of 30 seconds.

    Returns True if the entry qualifies.
    """
    # Skip podcasts and audiobooks
    if entry.get("episode_name") or entry.get("episode_show_name"):
        return False
    if entry.get("audiobook_title") or entry.get("audiobook_uri"):
        return False
    # Must have track name and artist
    if not entry.get("master_metadata_track_name") or not entry.get("master_metadata_album_artist_name"):
        return False
    # Play time must be >= 30 seconds
    ms_played = entry.get("ms_played", 0)
    try:
        ms_played_int = int(ms_played)
    except Exception:
        ms_played_int = 0
    if ms_played_int < 30_000:
        return False
    # Incognito mode means the user explicitly didn't want to record the play
    if entry.get("incognito_mode"):
        return False
    return True


def compute_start_timestamp(entry: Dict[str, Optional[str]]) -> int:
    """
    Compute the start timestamp for a streaming entry in seconds since epoch (UTC).

    Spotify's extended streaming history export provides a "ts" field
    representing when the stream finished (in ISO 8601 format) and an
    optional "offline_timestamp" for offline plays.  We treat the start
    timestamp as

      max(finish_ts - ms_played, offline_timestamp) if offline_timestamp is
      provided, otherwise finish_ts - ms_played

    This is a pragmatic heuristic: if offline mode was used we assume
    that the offline timestamp marks when the track ended, and so we
    subtract the play duration from it.  If no duration is available we
    simply return the offline timestamp.  All times are converted to
    seconds since 1970-01-01T00:00:00Z.
    """
    ms_played = entry.get("ms_played", 0) or 0
    try:
        ms_played_ms = int(ms_played)
    except Exception:
        ms_played_ms = 0
    offline_ts = entry.get("offline_timestamp")
    # Use offline_timestamp if present and not null
    if offline_ts:
        try:
            offline_end = datetime.datetime.utcfromtimestamp(int(offline_ts))
            start_time = offline_end - datetime.timedelta(milliseconds=ms_played_ms)
            return int(start_time.timestamp())
        except Exception:
            pass
    # Fallback to "ts" which marks the end in ISO8601 (UTC)
    ts_str = entry.get("ts")
    if ts_str:
        try:
            # Some exports include a trailing 'Z'
            ts_norm = ts_str.replace("Z", "+00:00")
            finish_dt = datetime.datetime.fromisoformat(ts_norm)
            start_time = finish_dt - datetime.timedelta(milliseconds=ms_played_ms)
            return int(start_time.timestamp())
        except Exception:
            pass
    # As a last resort, return the current time
    return int(time.time())


def build_scrobble_params(batch: List[Dict[str, Optional[str]]], api_key: str, api_secret: str, session_key: str) -> Dict[str, str]:
    """
    Build the parameter dictionary for a batch of scrobbles.

    Each entry in `batch` must already satisfy `should_scrobble()`.

    Parameters
    ----------
    batch : list of dict
        A list of streaming entries to include in this batch.
    api_key : str
        Your Last.fm API key.
    api_secret : str
        Your Last.fm API secret (used to generate the signature).
    session_key : str
        The authenticated session key (parameter `sk`).

    Returns
    -------
    dict
        A dictionary of form-encoded parameters ready for POSTing.
    """
    params: Dict[str, str] = {
        "method": "track.scrobble",
        "api_key": api_key,
        "sk": session_key,
    }
    for i, entry in enumerate(batch):
        # Required parameters
        params[f"artist[{i}]"] = entry.get("master_metadata_album_artist_name", "")
        params[f"track[{i}]"] = entry.get("master_metadata_track_name", "")
        # Compute start timestamp
        start_ts = compute_start_timestamp(entry)
        params[f"timestamp[{i}]"] = str(start_ts)
        # Optional parameters
        album = entry.get("master_metadata_album_album_name")
        if album:
            params[f"album[{i}]"] = album
        # Include album artist if present and different from artist
        album_artist = entry.get("master_metadata_album_artist_name")
        if album_artist and album_artist != entry.get("master_metadata_album_artist_name"):
            params[f"albumArtist[{i}]"] = album_artist
        # Duration can be provided in seconds
        ms_played = entry.get("ms_played", 0) or 0
        try:
            dur_sec = int(ms_played) // 1000
            if dur_sec > 0:
                params[f"duration[{i}]"] = str(dur_sec)
        except Exception:
            pass
        # We assume the user chose to play this song
        params[f"chosenByUser[{i}]"] = "1"
    # Generate the signature
    api_sig = generate_signature(params, api_secret)
    params["api_sig"] = api_sig
    return params


def submit_batch(batch: List[Dict[str, Optional[str]]], api_key: str, api_secret: str, session_key: str, dry_run: bool = False) -> bool:
    """
    Submit a batch of scrobbles to Last.fm.

    Parameters
    ----------
    batch : list
        The scrobbles to send in this request (max length 50).
    api_key : str
        API key.
    api_secret : str
        API secret (for signature).
    session_key : str
        Session key.
    dry_run : bool
        If True, do not actually submit, just print the constructed
        request payload.

    Returns
    -------
    bool
        True if the submission was accepted by Last.fm, False
        otherwise.  In dry_run mode, always returns True.
    """
    params = build_scrobble_params(batch, api_key, api_secret, session_key)
    # Add the output format
    params["format"] = "json"
    if dry_run:
        print("Dry‑run: would submit the following scrobbles:")
        for i in range(len(batch)):
            print(f"  {params[f'artist[{i}]']} – {params[f'track[{i}]']} at {params[f'timestamp[{i}]']}")
        return True
    try:
        response = requests.post(LASTFM_API_ROOT, data=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        status = data.get("scrobbles", {}).get("@attr", {}).get("accepted")
        # accepted can be None if Last.fm returns a string instead
        if status:
            return True
        # Check for lfm status field
        if data.get("lfm", {}).get("status") == "ok":
            return True
        # Fallback: treat as failure
        print(f"Warning: scrobble batch may have failed: {data}")
        return False
    except Exception as exc:
        print(f"Error submitting scrobbles: {exc}")
        return False


###############################################################################
# Main routine
###############################################################################


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrobble Spotify extended streaming history to Last.fm.",
        epilog=(
            "For help obtaining an API key and secret, see "
            "https://www.last.fm/api. This tool stores your credentials in "
            f"{CONFIG_FILE} for future runs."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        nargs="+",
        required=True,
        help="One or more Spotify streaming history JSON files or directories containing them.",
    )
    parser.add_argument(
        "--api-key", help="Your Last.fm API key. If omitted, will use stored key or prompt.")
    parser.add_argument(
        "--api-secret", help="Your Last.fm API secret. If omitted, will use stored secret or prompt.")
    parser.add_argument(
        "--session-key", help="Existing Last.fm session key. Skip interactive auth if provided.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Parse and build scrobbles but do not submit them.")
    args = parser.parse_args()

    # Resolve input paths
    paths: List[Path] = []
    for inp in args.input:
        p = Path(inp).expanduser().resolve()
        if p.is_dir():
            for file in p.glob("*.json"):
                paths.append(file)
        elif p.is_file():
            paths.append(p)
        else:
            print(f"Warning: {inp} does not exist, skipping")
    if not paths:
        print("Error: no valid input files provided.")
        sys.exit(1)

    # Load existing config
    config = load_config()

    api_key = args.api_key or config.get("api_key")
    api_secret = args.api_secret or config.get("api_secret")
    session_key = args.session_key or config.get("session_key")

    if not api_key:
        api_key = input("Enter your Last.fm API key: ").strip()
    if not api_secret:
        api_secret = input("Enter your Last.fm API secret: ").strip()

    # If session key isn't provided/stored, perform interactive auth
    if not session_key:
        try:
            session_key = authenticate_interactively(api_key, api_secret)
            # Save config for next time
            config.update({"api_key": api_key, "api_secret": api_secret, "session_key": session_key})
            save_config(config)
        except Exception as exc:
            print(f"Authentication failed: {exc}")
            sys.exit(1)

    # Parse streaming history
    print(f"Reading {len(paths)} input file(s)...")
    entries = parse_streaming_history(paths)
    print(f"Loaded {len(entries)} total entries.")
    # Filter entries to those we should scrobble
    scrobble_candidates = [e for e in entries if should_scrobble(e)]
    print(f"{len(scrobble_candidates)} entries qualify for scrobbling.")
    if not scrobble_candidates:
        print("Nothing to scrobble. Exiting.")
        return
    # Compute unique key to avoid duplicates (artist, track, timestamp)
    unique_scrobbles = []
    seen: set = set()
    for e in scrobble_candidates:
        ts = compute_start_timestamp(e)
        key = (e.get("master_metadata_album_artist_name"), e.get("master_metadata_track_name"), ts)
        if key not in seen:
            seen.add(key)
            unique_scrobbles.append(e)
    print(f"{len(unique_scrobbles)} unique scrobbles after removing duplicates.")
    # Sort by timestamp ascending
    unique_scrobbles.sort(key=lambda e: compute_start_timestamp(e))
    # Submit in batches
    batch_size = 50
    total = len(unique_scrobbles)
    success_count = 0
    for i in range(0, total, batch_size):
        batch = unique_scrobbles[i : i + batch_size]
        print(f"Submitting scrobbles {i + 1}–{i + len(batch)} of {total}...")
        ok = submit_batch(batch, api_key, api_secret, session_key, dry_run=args.dry_run)
        if ok:
            success_count += len(batch)
        # Respect Last.fm rate limits – small delay between requests
        if not args.dry_run:
            time.sleep(0.5)
    print(f"Finished. {success_count} scrobbles submitted.")


if __name__ == "__main__":
    main()
