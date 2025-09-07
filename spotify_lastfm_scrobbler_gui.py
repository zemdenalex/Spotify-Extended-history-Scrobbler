#!/usr/bin/env python3
"""
A minimal Tkinter front‑end for the spotify_lastfm_scrobbler.

This GUI wraps the core functionality in a simple window that asks for
your Last.fm API credentials and lets you choose a directory, zip
archive or individual JSON files containing your Spotify streaming
history.  Progress messages are displayed in a scrolling text area.

Run this script directly with Python or package it along with
spotify_lastfm_scrobbler.py using PyInstaller (see instructions in
spotify_lastfm_scrobbler.py).  Both files must reside in the same
directory so that the GUI can import the scrobbling functions.
"""

import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from pathlib import Path
from typing import List

try:
    # Import scrobbling functions from the CLI script.  If you move this
    # file, make sure to adjust the import accordingly.
    from spotify_lastfm_scrobbler import (
        load_config,
        save_config,
        authenticate_interactively,
        parse_streaming_history,
        should_scrobble,
        compute_start_timestamp,
        submit_batch,
    )
except Exception as exc:
    raise RuntimeError(
        "Failed to import scrobbling functions. Ensure that "
        "spotify_lastfm_scrobbler.py is located next to this script."
    ) from exc


class ScrobbleGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Spotify → Last.fm Scrobbler")
        self.geometry("600x500")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        # Load stored config if available
        self.config_data = load_config()
        # UI variables
        self.var_api_key = tk.StringVar(value=self.config_data.get("api_key", ""))
        self.var_api_secret = tk.StringVar(value=self.config_data.get("api_secret", ""))
        self.var_session_key = tk.StringVar(value=self.config_data.get("session_key", ""))
        self.var_input_paths = tk.StringVar()
        self.selected_paths: List[str] = []
        # Build UI
        self.create_widgets()

    def create_widgets(self) -> None:
        # API Key
        lbl_key = tk.Label(self, text="API Key:")
        lbl_key.grid(row=0, column=0, sticky="e", padx=5, pady=5)
        ent_key = tk.Entry(self, textvariable=self.var_api_key, width=50)
        ent_key.grid(row=0, column=1, columnspan=2, sticky="w", padx=5, pady=5)
        # API Secret
        lbl_secret = tk.Label(self, text="API Secret:")
        lbl_secret.grid(row=1, column=0, sticky="e", padx=5, pady=5)
        ent_secret = tk.Entry(self, textvariable=self.var_api_secret, width=50)
        ent_secret.grid(row=1, column=1, columnspan=2, sticky="w", padx=5, pady=5)
        # Session Key (optional)
        lbl_sk = tk.Label(self, text="Session Key (opt):")
        lbl_sk.grid(row=2, column=0, sticky="e", padx=5, pady=5)
        ent_sk = tk.Entry(self, textvariable=self.var_session_key, width=50)
        ent_sk.grid(row=2, column=1, columnspan=2, sticky="w", padx=5, pady=5)
        # Input selection
        lbl_input = tk.Label(self, text="Input Files/Dir:")
        lbl_input.grid(row=3, column=0, sticky="e", padx=5, pady=5)
        ent_input = tk.Entry(self, textvariable=self.var_input_paths, width=40, state="readonly")
        ent_input.grid(row=3, column=1, sticky="w", padx=5, pady=5)
        btn_browse_files = tk.Button(self, text="Browse Files", command=self.select_files)
        btn_browse_files.grid(row=3, column=2, padx=5, pady=5)
        btn_browse_dir = tk.Button(self, text="Browse Dir", command=self.select_directory)
        btn_browse_dir.grid(row=4, column=2, padx=5, pady=5)
        # Run button
        self.btn_run = tk.Button(self, text="Start Scrobbling", command=self.start_scrobbling)
        self.btn_run.grid(row=4, column=1, sticky="w", padx=5, pady=5)
        # Output area
        self.txt_output = scrolledtext.ScrolledText(self, height=20, width=70, state="disabled")
        self.txt_output.grid(row=5, column=0, columnspan=3, padx=5, pady=5)

    def log(self, message: str) -> None:
        """Append a message to the output area."""
        self.txt_output.configure(state="normal")
        self.txt_output.insert(tk.END, message + "\n")
        self.txt_output.configure(state="disabled")
        self.txt_output.see(tk.END)

    def select_files(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self,
            title="Select JSON or ZIP files",
            filetypes=[("JSON/ZIP", "*.json *.zip"), ("All files", "*.*")],
        )
        if paths:
            self.selected_paths = list(paths)
            self.var_input_paths.set("; ".join(paths))

    def select_directory(self) -> None:
        path = filedialog.askdirectory(parent=self, title="Select Directory")
        if path:
            self.selected_paths = [path]
            self.var_input_paths.set(path)

    def start_scrobbling(self) -> None:
        # Validate inputs
        api_key = self.var_api_key.get().strip()
        api_secret = self.var_api_secret.get().strip()
        session_key = self.var_session_key.get().strip() or None
        if not api_key or not api_secret:
            messagebox.showerror("Missing API Credentials", "Please enter your API key and secret.")
            return
        if not self.selected_paths:
            messagebox.showerror("Missing Input", "Please select at least one file or directory.")
            return
        # Disable run button
        self.btn_run.configure(state="disabled")
        # Clear output
        self.txt_output.configure(state="normal")
        self.txt_output.delete("1.0", tk.END)
        self.txt_output.configure(state="disabled")
        # Run scrobbling in a separate thread
        threading.Thread(
            target=self.scrobble_worker,
            args=(api_key, api_secret, session_key, list(self.selected_paths)),
            daemon=True,
        ).start()

    def scrobble_worker(self, api_key: str, api_secret: str, session_key: str, paths: List[str]) -> None:
        try:
            # Authenticate if necessary
            if not session_key:
                self.log("No session key provided – starting interactive authentication...")
                session_key_new = authenticate_interactively(api_key, api_secret)
                session_key = session_key_new
                # Persist session key
                self.config_data.update({"api_key": api_key, "api_secret": api_secret, "session_key": session_key})
                save_config(self.config_data)
            # Convert string paths to Path objects
            path_objs = []
            for p in paths:
                po = Path(p)
                if po.is_dir():
                    for file in po.glob("*.json"):
                        path_objs.append(file)
                    for file in po.glob("*.zip"):
                        path_objs.append(file)
                else:
                    path_objs.append(po)
            self.log(f"Reading {len(path_objs)} input file(s)...")
            entries = parse_streaming_history(path_objs)
            self.log(f"Loaded {len(entries)} total entries.")
            scrobs = [e for e in entries if should_scrobble(e)]
            self.log(f"{len(scrobs)} entries qualify for scrobbling.")
            if not scrobs:
                self.log("Nothing to scrobble.")
                return
            # Deduplicate
            seen = set()
            unique = []
            for e in scrobs:
                ts = compute_start_timestamp(e)
                key = (e.get("master_metadata_album_artist_name"), e.get("master_metadata_track_name"), ts)
                if key not in seen:
                    seen.add(key)
                    unique.append(e)
            self.log(f"{len(unique)} unique scrobbles after removing duplicates.")
            # Sort chronologically
            unique.sort(key=lambda e: compute_start_timestamp(e))
            batch_size = 50
            total = len(unique)
            submitted = 0
            for i in range(0, total, batch_size):
                batch = unique[i : i + batch_size]
                self.log(f"Submitting scrobbles {i + 1}–{i + len(batch)} of {total}...")
                ok = submit_batch(batch, api_key, api_secret, session_key, dry_run=False)
                if ok:
                    submitted += len(batch)
                else:
                    self.log("Warning: some scrobbles may have failed.")
                # small delay to avoid hammering the API
                time.sleep(0.5)
            self.log(f"Finished. {submitted} scrobbles submitted.")
        except Exception as exc:
            self.log(f"Error: {exc}")
        finally:
            self.btn_run.configure(state="normal")

    def on_close(self) -> None:
        self.destroy()


def main() -> None:
    app = ScrobbleGUI()
    app.mainloop()


if __name__ == "__main__":
    main()