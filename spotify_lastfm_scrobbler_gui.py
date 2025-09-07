#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, subprocess, threading, queue, shlex, datetime as dt
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PY_EXE = sys.executable

def build_args(values: dict) -> list[str]:
    args = [PY_EXE, os.path.join(APP_DIR, "spotify_lastfm_scrobbler.py")]
    args += ["--input", values["input"]]
    if values["since"]: args += ["--since", values["since"]]
    if values["until"]: args += ["--until", values["until"]]
    if values["limit"]: args += ["--limit", str(values["limit"])]
    if values["include_duration"]: args += ["--include-duration"]
    if values["no_chosen"]: args += ["--no-chosen-by-user"]
    if values["import_mode"]:
        args += ["--import-mode", "--gap-sec", str(values["gap"]), "--finish-at", values["finish_at"] or "now"]
    if values["state_file"]:
        args += ["--state-file", values["state_file"]]
    if values["debug"]: args += ["--debug"]
    return args

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Spotify → Last.fm Scrobbler")
        self.geometry("920x620")
        self.proc = None
        self.q = queue.Queue()
        self._build_ui()

    def _build_ui(self):
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # input picker
        self.input_var = tk.StringVar()
        r = 0
        ttk.Label(frm, text="Input (dir or ZIP/JSON):").grid(row=r, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.input_var, width=80).grid(row=r, column=1, sticky="we")
        ttk.Button(frm, text="Browse", command=self.pick_input).grid(row=r, column=2, padx=5); r+=1

        # date filters
        self.since_var = tk.StringVar(); self.until_var = tk.StringVar()
        ttk.Label(frm, text="Since (YYYY-MM-DD):").grid(row=r, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.since_var, width=18).grid(row=r, column=1, sticky="w")
        ttk.Label(frm, text="Until (YYYY-MM-DD):").grid(row=r, column=1, sticky="e", padx=(0,210))
        ttk.Entry(frm, textvariable=self.until_var, width=18).grid(row=r, column=1, sticky="e"); r+=1

        # options
        self.limit_var = tk.StringVar()
        self.inc_dur_var = tk.BooleanVar(value=True)
        self.no_chosen_var = tk.BooleanVar(value=True)
        self.debug_var = tk.BooleanVar(value=False)

        ttk.Label(frm, text="Limit (optional):").grid(row=r, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.limit_var, width=10).grid(row=r, column=1, sticky="w")
        ttk.Checkbutton(frm, text="Include duration", variable=self.inc_dur_var).grid(row=r, column=1, sticky="w", padx=(120,0))
        ttk.Checkbutton(frm, text="No chosenByUser", variable=self.no_chosen_var).grid(row=r, column=1, sticky="w", padx=(270,0))
        ttk.Checkbutton(frm, text="Debug log", variable=self.debug_var).grid(row=r, column=1, sticky="w", padx=(420,0)); r+=1

        # import mode
        self.import_var = tk.BooleanVar(value=True)
        self.gap_var = tk.StringVar(value="1")
        self.finish_var = tk.StringVar(value="now")
        self.state_var = tk.StringVar(value=os.path.join(APP_DIR, "lastfm_resume_state.json"))

        ttk.Checkbutton(frm, text="Import mode (re-date plays)", variable=self.import_var).grid(row=r, column=0, sticky="w")
        ttk.Label(frm, text="Gap (sec):").grid(row=r, column=1, sticky="w")
        ttk.Entry(frm, textvariable=self.gap_var, width=6).grid(row=r, column=1, sticky="w", padx=(65,0))
        ttk.Label(frm, text='Finish at ("now" or ISO):').grid(row=r, column=1, sticky="w", padx=(120,0))
        ttk.Entry(frm, textvariable=self.finish_var, width=22).grid(row=r, column=1, sticky="w", padx=(280,0)); r+=1

        ttk.Label(frm, text="State file:").grid(row=r, column=0, sticky="w")
        ttk.Entry(frm, textvariable=self.state_var, width=60).grid(row=r, column=1, sticky="we")
        ttk.Button(frm, text="…", width=3, command=self.pick_state).grid(row=r, column=2); r+=1

        # buttons
        btns = ttk.Frame(frm); btns.grid(row=r, column=0, columnspan=3, pady=6, sticky="w")
        ttk.Button(btns, text="Start / Resume", command=self.start).pack(side="left", padx=3)
        ttk.Button(btns, text="Stop", command=self.stop).pack(side="left", padx=3)
        ttk.Button(btns, text="Create Startup Task", command=self.create_task).pack(side="left", padx=12)
        r+=1

        # log
        self.log = tk.Text(frm, height=20, wrap="word")
        self.log.grid(row=r, column=0, columnspan=3, sticky="nsew")
        frm.rowconfigure(r, weight=1)
        frm.columnconfigure(1, weight=1)

        self.after(200, self._drain_log)

    def pick_input(self):
        p = filedialog.askdirectory(title="Pick Spotify Extended Streaming History folder")
        if p: self.input_var.set(p)

    def pick_state(self):
        p = filedialog.asksaveasfilename(title="Pick state file", defaultextension=".json",
                                         initialfile="lastfm_resume_state.json")
        if p: self.state_var.set(p)

    def append(self, line: str):
        self.log.insert("end", line + "\n")
        self.log.see("end")

    def _drain_log(self):
        try:
            while True:
                self.append(self.q.get_nowait())
        except queue.Empty:
            pass
        self.after(200, self._drain_log)

    def _reader(self, proc: subprocess.Popen):
        for line in iter(proc.stdout.readline, ""):
            self.q.put(line.rstrip())
        proc.wait()
        rc = proc.returncode
        self.q.put(f"\nProcess exit code: {rc}")

    def start(self):
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("Already running", "The scrobbling process is already running.")
            return
        vals = {
            "input": self.input_var.get().strip(),
            "since": self.since_var.get().strip(),
            "until": self.until_var.get().strip(),
            "limit": self.limit_var.get().strip(),
            "include_duration": self.inc_dur_var.get(),
            "no_chosen": self.no_chosen_var.get(),
            "debug": self.debug_var.get(),
            "import_mode": self.import_var.get(),
            "gap": int(self.gap_var.get() or "1"),
            "finish_at": self.finish_var.get().strip() or "now",
            "state_file": self.state_var.get().strip(),
        }
        if not vals["input"]:
            messagebox.showerror("Missing input", "Please select your Spotify history folder/zip/json.")
            return
        if vals["limit"] and not vals["limit"].isdigit():
            messagebox.showerror("Bad limit", "Limit must be a number.")
            return

        args = build_args(vals)
        self.append("Running: " + " ".join(shlex.quote(a) for a in args))
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1, cwd=APP_DIR)
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.append("Sent terminate signal.")
        else:
            self.append("Not running.")

    def create_task(self):
        vals = {
            "input": self.input_var.get().strip(),
            "since": self.since_var.get().strip(),
            "until": self.until_var.get().strip(),
            "limit": self.limit_var.get().strip(),
            "include_duration": self.inc_dur_var.get(),
            "no_chosen": self.no_chosen_var.get(),
            "debug": self.debug_var.get(),
            "import_mode": self.import_var.get(),
            "gap": int(self.gap_var.get() or "1"),
            "finish_at": self.finish_var.get().strip() or "now",
            "state_file": self.state_var.get().strip(),
        }
        args = build_args(vals)
        cmdline = " ".join(shlex.quote(a) for a in args)
        bat_path = os.path.join(APP_DIR, "run_lastfm_import.bat")
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(f'@echo off\ncd /d "{APP_DIR}"\n{cmdline} >> run.log 2>&1\n')
        schtasks = [
            "schtasks", "/Create", "/F", "/SC", "ONLOGON", "/RL", "HIGHEST",
            "/TN", "LastFm Import Resume",
            "/TR", rf"\"%SystemRoot%\System32\cmd.exe\" /c \"{bat_path}\""
        ]
        try:
            out = subprocess.check_output(schtasks, text=True, stderr=subprocess.STDOUT)
            messagebox.showinfo("Startup task", "Created/updated startup task.\n\n" + out)
        except subprocess.CalledProcessError as e:
            messagebox.showerror("Failed", f"Could not create task.\n\n{e.output}")

if __name__ == "__main__":
    App().mainloop()
