#!/usr/bin/env python3
"""Plug in a card -> overnight run starts. Background watcher for GoPro / Mission 1 / DJI cards.

    python scripts/card_watcher.py                 # run in the foreground (Ctrl+C to stop)
    pythonw scripts/card_watcher.py                # Windows, no console (what the login task uses)
    python scripts/card_watcher.py --once --dry-run   # check what it would do right now

Every few seconds it looks for mounted volumes with a DCIM folder (Windows drive letters, macOS /Volumes,
Linux /media and /run/media). A card is processed when:
  * its file list has stopped changing for two polls (no half-mounted or still-copying cards),
  * its content fingerprint (names + sizes of the video files) hasn't been processed before,
  * no other run is in progress (one at a time; a second card waits its turn).
Then it starts run_session.py --overnight with session "<date>_<volume label>". It only ever READS the card:
nothing on it is deleted, moved or formatted. Log: <library>/../Logs/card_watcher.log
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import string
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from fpvpipe import config  # noqa: E402

VIDEO = {".mp4", ".mov"}


def candidate_roots() -> list[Path]:
    if sys.platform == "win32":
        return [Path(f"{d}:/") for d in string.ascii_uppercase[3:] if os.path.exists(f"{d}:/")]   # D: .. Z:
    roots = []
    if Path("/Volumes").is_dir():                               # macOS: /Volumes/<card>
        roots += [p for p in Path("/Volumes").iterdir() if p.is_dir()]
    for base in (Path("/media"), Path("/run/media")):          # Linux: /media/<card> or /media/<user>/<card>
        if base.is_dir():
            for p in base.iterdir():
                if p.is_dir():
                    roots += [p] + [q for q in p.iterdir() if q.is_dir()]
    return roots


def volume_label(root: Path) -> str:
    if sys.platform == "win32" and root.parent == root:          # drive root (E:\): use the volume label
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(261)
            if ctypes.windll.kernel32.GetVolumeInformationW(str(root), buf, 261, None, None, None, None, 0):
                if buf.value:
                    return buf.value
        except Exception:
            pass
        return f"card{root.drive[:1]}"
    return root.name


def fingerprint(root: Path) -> tuple[str, int]:
    """Hash of video file names + sizes under DCIM (cheap: no file contents read). Returns (hash, n_files)."""
    items = []
    for dirpath, _, files in os.walk(root / "DCIM"):
        for f in files:
            if Path(f).suffix.lower() in VIDEO and not f.startswith("."):
                try:
                    items.append(f"{f}:{os.path.getsize(os.path.join(dirpath, f))}")
                except OSError:
                    pass
    items.sort()
    return hashlib.sha1("\n".join(items).encode()).hexdigest()[:20], len(items)


def safe_name(label: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label).strip("_") or "card"


class Watcher:
    def __init__(self, state_file: Path, log_file: Path, roots_fn=candidate_roots, dry_run: bool = False,
                 extra_args: list[str] | None = None):
        self.state_file, self.log_file = state_file, log_file
        self.roots_fn, self.dry_run, self.extra = roots_fn, dry_run, extra_args or []
        self.pending: dict[str, str] = {}   # root -> fingerprint seen on the previous poll
        self.proc: subprocess.Popen | None = None
        self.queue: list[tuple[Path, str]] = []
        self.started: list[list[str]] = []  # commands launched (for --dry-run and tests)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(self, msg: str):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def done(self) -> set[str]:
        try:
            return set(json.loads(self.state_file.read_text()))
        except (OSError, ValueError):
            return set()

    def mark(self, fp: str):
        d = self.done() | {fp}
        self.state_file.write_text(json.dumps(sorted(d)))

    def poll(self):
        seen = set()
        for root in self.roots_fn():
            if not (root / "DCIM").is_dir():
                continue
            key = str(root)
            seen.add(key)
            fp, n = fingerprint(root)
            if n == 0 or fp in self.done() or any(q[1] == fp for q in self.queue):
                self.pending.pop(key, None)
                continue
            if self.pending.get(key) == fp:          # unchanged for two polls: stable, queue it
                self.queue.append((root, fp))
                self.pending.pop(key, None)
                self.log(f"card {volume_label(root)} at {root}: {n} video files, queued")
            else:
                self.pending[key] = fp
        for k in list(self.pending):
            if k not in seen:
                self.pending.pop(k)
        self._run_next()

    def _run_next(self):
        if self.proc is not None and self.proc.poll() is None:
            return
        if self.proc is not None:
            self.log(f"run finished (exit {self.proc.returncode})")
            self.proc = None
        if not self.queue:
            return
        root, fp = self.queue.pop(0)
        session = f"{date.today().isoformat()}_{safe_name(volume_label(root))}"
        cmd = [sys.executable, str(HERE / "run_session.py"), str(root / "DCIM"), "--session", session,
               "--overnight"] + self.extra
        self.started.append(cmd)
        self.mark(fp)                                # mark first: a crash mid-run must not loop on the same card
        if self.dry_run:
            self.log("would run: " + " ".join(cmd))
            return
        self.log(f"starting overnight run: session {session}")
        out = open(self.log_file.with_name(f"{session}.log"), "a", encoding="utf-8")
        flags = 0x08000000 if sys.platform == "win32" else 0     # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, creationflags=flags)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--once", action="store_true", help="two polls and exit (shows what would happen)")
    ap.add_argument("--dry-run", action="store_true", help="log the command instead of starting it")
    ap.add_argument("--forget", action="store_true", help="clear the list of processed cards")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    logs = cfg["paths"]["library"].parent / "Logs"
    w = Watcher(logs / "card_watcher_state.json", logs / "card_watcher.log", dry_run=args.dry_run,
                extra_args=["--config", str(args.config)] if args.config else [])
    if args.forget:
        w.state_file.write_text("[]")
        w.log("processed-card list cleared")
    w.log(f"watching for cards (every {args.interval:.0f}s)")
    polls = 0
    try:
        while True:
            w.poll()
            polls += 1
            if args.once and polls >= 2:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        w.log("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
