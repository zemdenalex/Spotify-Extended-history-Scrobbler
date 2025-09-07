# --- FILE: spotify_lastfm_scrobbler_gui.py ---
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spotify → Last.fm Scrobbler (GUI) — v0.8
- Tk/ttk UI, progress bar, collapsible Advanced options.
- Buttons: Authenticate/Reset, Probe, Start, Open Log.
- Mirrors CLI features: include duration, omit chosenByUser, date range, debug.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import List

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Import core functions from CLI module (must be alongside this file)
from spotify_lastfm_scrobbler import (
    load_config,
    save_config,
    delete_config,
    authenticate_interactively,
    parse_streaming_history,
    should_scrobble,
    compute_start_timestamp,
    submit_batch,
)

DEBUG_LOG = Path("scrobble_debug.log")

class ScrobbleGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Spotify → Last.fm Scrobbler")
        self.geometry("760x640")
        self.minsize(720, 600)
        self.style = ttk.Style(self)
        # Prefer a modern theme if available
        for theme in ("vista", "xpnative", "clam"):  # best-effort on Windows/mac/Linux
            try:
                self.style.theme_use(theme)
                break
            except Exception:
                continue

        self.cfg = load_config()
        self.vars = {
            "api_key": tk.StringVar(value=self.cfg.get("api_key", "")),
            "api_secret": tk.StringVar(value=self.cfg.get("api_secret", "")),
            "session_key": tk.StringVar(value=self.cfg.get("session_key", "")),
            "paths_str": tk.StringVar(value=""),
            "since": tk.StringVar(value=""),
            "until": tk.StringVar(value=""),
            "include_duration": tk.BooleanVar(value=True),
            "no_chosen": tk.BooleanVar(value=False),
            "debug": tk.BooleanVar(value=True),
            "dry_run": tk.BooleanVar(value=False),
            "limit": tk.StringVar(value="")
        }
        self.selected_paths: List[str] = []
        self._build_ui()

    # UI layout
    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="API Key:").grid(row=0, column=0, sticky="e")
        ttk.Entry(top, textvariable=self.vars["api_key"], width=48).grid(row=0, column=1, sticky="we")
        ttk.Button(top, text="Authenticate", command=self.on_auth).grid(row=0, column=2, sticky="w")

        ttk.Label(top, text="API Secret:").grid(row=1, column=0, sticky="e")
        ttk.Entry(top, textvariable=self.vars["api_secret"], width=48, show="•").grid(row=1, column=1, sticky="we")
        ttk.Button(top, text="Reset Auth", command=self.on_reset_auth).grid(row=1, column=2, sticky="w")

        ttk.Label(top, text="Session Key (optional):").grid(row=2, column=0, sticky="e")
        ttk.Entry(top, textvariable=self.vars["session_key"], width=48).grid(row=2, column=1, sticky="we")
        ttk.Button(top, text="Probe", command=self.on_probe).grid(row=2, column=2, sticky="w")

        top.columnconfigure(1, weight=1)

        pathf = ttk.LabelFrame(self, text="Input")
        pathf.pack(fill="x", **pad)
        ttk.Entry(pathf, textvariable=self.vars["paths_str"], state="readonly").grid(row=0, column=0, sticky="we")
        ttk.Button(pathf, text="Browse Files", command=self.on_browse_files).grid(row=0, column=1, sticky="w")
        ttk.Button(pathf, text="Browse Folder", command=self.on_browse_dir).grid(row=0, column=2, sticky="w")
        pathf.columnconfigure(0, weight=1)

        adv = ttk.LabelFrame(self, text="Advanced")
        adv.pack(fill="x", **pad)
        ttk.Checkbutton(adv, text="Include duration", variable=self.vars["include_duration"]).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(adv, text="Omit chosenByUser", variable=self.vars["no_chosen"]).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(adv, text="Debug log", variable=self.vars["debug"]).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(adv, text="Dry run (no writes)", variable=self.vars["dry_run"]).grid(row=0, column=3, sticky="w")
        ttk.Label(adv, text="Since (YYYY-MM-DD):").grid(row=1, column=0, sticky="e")
        ttk.Entry(adv, textvariable=self.vars["since"], width=14).grid(row=1, column=1, sticky="w")
        ttk.Label(adv, text="Until (YYYY-MM-DD):").grid(row=1, column=2, sticky="e")
        ttk.Entry(adv, textvariable=self.vars["until"], width=14).grid(row=1, column=3, sticky="w")
        ttk.Label(adv, text="Limit (count):").grid(row=1, column=4, sticky="e")
        ttk.Entry(adv, textvariable=self.vars["limit"], width=10).grid(row=1, column=5, sticky="w")

        ctrl = ttk.Frame(self)
        ctrl.pack(fill="x", **pad)
        self.btn_start = ttk.Button(ctrl, text="Start Scrobbling", command=self.on_start)
        self.btn_start.pack(side="left")
        ttk.Button(ctrl, text="Open Log", command=self.on_open_log).pack(side="left", padx=8)

        progf = ttk.Frame(self)
        progf.pack(fill="x", **pad)
        self.progress = ttk.Progressbar(progf, mode="determinate")
        self.progress.pack(fill="x")

        outf = ttk.LabelFrame(self, text="Output")
        outf.pack(fill="both", expand=True, **pad)
        self.txt = tk.Text(outf, height=18, font=("Consolas", 10))
        self.txt.pack(fill="both", expand=True)

    # UI helpers
    def log(self, msg: str) -> None:
        self.txt.insert("end", msg + "\n"); self.txt.see("end")

    def on_browse_files(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self, title="Select JSON/ZIP files", filetypes=[("JSON/ZIP", "*.json *.zip"), ("All files", "*.*")]
        )
        if paths:
            self.selected_paths = list(paths)
            self.vars["paths_str"].set("; ".join(paths))

    def on_browse_dir(self) -> None:
        path = filedialog.askdirectory(parent=self, title="Select Folder")
        if path:
            self.selected_paths = [path]
            self.vars["paths_str"].set(path)

    def on_open_log(self) -> None:
        if DEBUG_LOG.exists():
            try:
                import os
                os.startfile(str(DEBUG_LOG))  # Windows
            except Exception:
                messagebox.showinfo("Debug log", f"See {DEBUG_LOG.resolve()}")
        else:
            messagebox.showinfo("Debug log", "No log file found yet.")

    def on_auth(self) -> None:
        api_key = self.vars["api_key"].get().strip()
        api_secret = self.vars["api_secret"].get().strip()
        if not api_key or not api_secret:
            messagebox.showerror("Missing", "Enter API key and secret first.")
            return
        try:
            sk = authenticate_interactively(api_key, api_secret)
            self.vars["session_key"].set(sk)
            cfg = load_config(); cfg.update({"api_key": api_key, "api_secret": api_secret, "session_key": sk}); save_config(cfg)
            messagebox.showinfo("Authenticated", "Authorization successful and saved.")
        except Exception as exc:
            messagebox.showerror("Auth failed", str(exc))

    def on_reset_auth(self) -> None:
        delete_config(); self.vars["session_key"].set(""); messagebox.showinfo("Reset", "Cleared cached credentials.")

    def on_probe(self) -> None:
        api_key = self.vars["api_key"].get().strip(); api_secret = self.vars["api_secret"].get().strip()
        if not api_key or not api_secret:
            messagebox.showerror("Missing", "Enter API key and secret first.")
            return
        # Reuse CLI probe by calling a tiny run in a thread
        self._run_thread(self._worker_probe, api_key, api_secret, self.vars["session_key"].get().strip())

    def on_start(self) -> None:
        api_key = self.vars["api_key"].get().strip(); api_secret = self.vars["api_secret"].get().strip()
        if not api_key or not api_secret:
            messagebox.showerror("Missing", "Enter API key and secret first.")
            return
        if not self.selected_paths:
            messagebox.showerror("Missing", "Select files or a folder.")
            return
        self.btn_start.configure(state="disabled")
        self.progress.configure(value=0, maximum=100)
        self.txt.delete("1.0", "end")
        self._run_thread(self._worker_start, api_key, api_secret, self.vars["session_key"].get().strip(), list(self.selected_paths))

    def _run_thread(self, target, *args):
        t = threading.Thread(target=target, args=args, daemon=True)
        t.start()

    # Workers
    def _worker_probe(self, api_key: str, api_secret: str, session_key: str) -> None:
        try:
            from spotify_lastfm_scrobbler import run_probe
            run_probe(api_key, api_secret, session_key, debug=self.vars["debug"].get())
            self.log("Probe sent. Check Last.fm recent tracks and the debug log if enabled.")
        except Exception as exc:
            self.log(f"Probe failed: {exc}")

    def _worker_start(self, api_key: str, api_secret: str, session_key: str, paths: List[str]) -> None:
        try:
            if not session_key:
                self.log("No session key — starting interactive auth...")
                sk = authenticate_interactively(api_key, api_secret)
                session_key = sk
                cfg = load_config(); cfg.update({"api_key": api_key, "api_secret": api_secret, "session_key": sk}); save_config(cfg)
                self.vars["session_key"].set(sk)

            # Collect inputs
            path_objs: List[Path] = []
            for p in paths:
                po = Path(p)
                path_objs.append(po)

            self.log(f"Reading inputs ({len(path_objs)})...")
            entries = parse_streaming_history(path_objs)
            self.log(f"Loaded {len(entries)} total entries.")

            scrobs = [e for e in entries if should_scrobble(e)]
            self.log(f"{len(scrobs)} entries qualify for scrobbling.")
            if not scrobs:
                self.log("Nothing to scrobble.")
                self.btn_start.configure(state="normal"); return

            # Range filter, dedupe
            since = self.vars["since"].get().strip() or None
            until = self.vars["until"].get().strip() or None
            def within(ts: int) -> bool:
                from spotify_lastfm_scrobbler import within_range
                return within_range(ts, since, until)

            seen = set(); unique: List[dict] = []
            for e in scrobs:
                ts = compute_start_timestamp(e)
                if not within(ts):
                    continue
                key = (e.get("master_metadata_album_artist_name"), e.get("master_metadata_track_name"), ts)
                if key not in seen:
                    seen.add(key)
                    unique.append(e)

            unique.sort(key=lambda e: compute_start_timestamp(e))
            limit_s = self.vars["limit"].get().strip()
            if limit_s:
                try:
                    unique = unique[: max(0, int(limit_s))]
                except Exception:
                    pass

            total = len(unique)
            self.log(f"{total} unique scrobbles after removing duplicates.")
            if total == 0:
                self.btn_start.configure(state="normal"); return

            submitted = 0
            for i in range(0, total, 50):
                batch = unique[i : i + 50]
                self.log(f"Submitting scrobbles {i+1}–{i+len(batch)} of {total}...")
                res = submit_batch(
                    batch,
                    api_key,
                    api_secret,
                    session_key,
                    dry_run=self.vars["dry_run"].get(),
                    debug=self.vars["debug"].get(),
                    include_duration=self.vars["include_duration"].get(),
                    send_chosen=(not self.vars["no_chosen"].get()),
                )
                if res.get("accepted", 0) > 0:
                    submitted += min(50, len(batch))
                else:
                    self.log(f"Warning: accepted={res.get('accepted',0)} ignored={res.get('ignored',0)}")
                self.progress.configure(value=(i + len(batch)) * 100 / max(1, total))
                time.sleep(0.5 if not self.vars["dry_run"].get() else 0)

            self.log("Finished. " + (f"{submitted} processed (dry-run)." if self.vars["dry_run"].get() else f"{submitted} scrobbles submitted."))
        except Exception as exc:
            self.log(f"Error: {exc}")
        finally:
            self.btn_start.configure(state="normal")


def main() -> None:
    app = ScrobbleGUI()
    app.mainloop()


if __name__ == "__main__":
    main()