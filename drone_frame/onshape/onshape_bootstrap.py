#!/usr/bin/env python3
"""Create the PF10 Onshape document from the pipeline outputs via the Onshape REST API.

    export ONSHAPE_ACCESS_KEY=...  ONSHAPE_SECRET_KEY=...      # dev portal -> API keys
    python onshape/onshape_bootstrap.py                         # dry run: prints the plan only
    python onshape/onshape_bootstrap.py --check                 # verify keys + network only (no changes)
    python onshape/onshape_bootstrap.py --execute               # creates the document

What it does (see SETUP.md for the manual equivalent):
  1. POST /documents                              -> new document "PF10 Frame <date>"
  2. POST /featurestudios/d/{did}/w/{wid}          -> one Feature Studio per generated .fs file
     POST /featurestudios/d/{did}/w/{wid}/e/{eid}  -> upload its contents
  3. POST /blobelements/d/{did}/w/{wid}            -> import each STEP with translation (creates Part Studios)

STATUS: written against the public Onshape REST API (v6 paths), NOT executed in the environment that
generated it (no credentials there). Run without --execute first. If an endpoint has moved, the error
message from Onshape names the field; fix it here and re-run. Nothing is deleted, ever.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "onshape" / "generated"
STEP = ROOT / "outputs" / "cad" / "step"
STUDIOS = [("FS PF10 Variables", "PF10FrameVariables.fs"), ("FS PF10 Arm", "PF10ModularArm.fs"),
           ("FS PF10 Socket", "PF10ArmSocket.fs")]


class Client:
    def __init__(self, base: str, access: str, secret: str):
        import requests  # imported lazily so a dry run needs nothing

        self.s = requests.Session()
        self.s.auth = (access, secret)          # Onshape API keys support HTTP basic auth
        self.s.headers.update({"Accept": "application/json;charset=UTF-8; qs=0.09"})
        self.base = base.rstrip("/") + "/api/v6"

    def post(self, path: str, **kw):
        r = self.s.post(self.base + path, timeout=120, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"POST {path} -> {r.status_code}: {r.text[:400]}{_hint(r.status_code)}")
        return r.json() if r.text else {}

    def get(self, path: str):
        r = self.s.get(self.base + path, timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"GET {path} -> {r.status_code}: {r.text[:400]}{_hint(r.status_code)}")
        return r.json()


def _hint(code: int) -> str:
    if code == 401:
        return ("\n  hint: keys rejected. Keys only work on the stack they were made on (cad.onshape.com vs a "
                "company domain: set ONSHAPE_BASE_URL), and the secret is shown only once at creation.")
    if code == 403:
        return "\n  hint: key lacks a scope. Create it with read + write scopes."
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true", help="actually call the API (default: dry run)")
    ap.add_argument("--check", action="store_true", help="only verify keys and network access, change nothing")
    ap.add_argument("--base-url", default=os.environ.get("ONSHAPE_BASE_URL", "https://cad.onshape.com"))
    ap.add_argument("--name", default=f"PF10 Frame {dt.date.today().isoformat()}")
    ap.add_argument("--no-step", action="store_true", help="skip STEP imports")
    args = ap.parse_args()

    fs_files = [(n, GEN / f) for n, f in STUDIOS]
    steps = sorted(STEP.glob("*.step")) if not args.no_step else []
    missing = [str(p) for _, p in fs_files if not p.exists()]
    if missing:
        print("Missing generated files - run `python run_pipeline.py` first:", *missing, sep="\n  ")
        return 1
    print(f"Plan for document '{args.name}' on {args.base_url}:")
    for n, p in fs_files:
        print(f"  Feature Studio  {n:20s} <- {p.relative_to(ROOT)} ({p.stat().st_size} B)")
    for p in steps:
        print(f"  STEP import     {p.name:20s} ({p.stat().st_size / 1e3:.0f} kB)")
    if not (args.execute or args.check):
        print("\nDry run only. Re-run with --check, then --execute (needs ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY).")
        return 0

    try:
        c = Client(args.base_url, os.environ["ONSHAPE_ACCESS_KEY"], os.environ["ONSHAPE_SECRET_KEY"])
    except KeyError:
        print("Set ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY (Onshape developer portal -> API keys).")
        return 1
    try:
        me = c.get("/users/sessioninfo")
    except Exception as exc:  # network policy, DNS, TLS or auth
        print(f"Cannot reach Onshape at {args.base_url}: {exc}")
        return 1
    print(f"Connected to {args.base_url} as {me.get('name') or me.get('email') or me.get('id')}")
    if args.check:
        return 0
    doc = c.post("/documents", json={"name": args.name, "isPublic": False})
    did, wid = doc["id"], doc["defaultWorkspace"]["id"]
    print(f"created document {did} (workspace {wid})")
    for name, path in fs_files:
        el = c.post(f"/featurestudios/d/{did}/w/{wid}", json={"name": name})
        eid = el["id"]
        cur = c.get(f"/featurestudios/d/{did}/w/{wid}/e/{eid}")
        body = {"contents": path.read_text(), "serializationVersion": cur.get("serializationVersion", "1.1.24"),
                "sourceMicroversion": cur.get("sourceMicroversion", ""), "rejectMicroversionSkew": False}
        c.post(f"/featurestudios/d/{did}/w/{wid}/e/{eid}", json=body)
        print(f"  feature studio {name} -> {eid}")
    for p in steps:
        with open(p, "rb") as fh:
            files = {"file": (p.name, fh, "application/octet-stream")}
            data = {"encodedFilename": p.name, "translate": "true", "formatName": "STEP",
                    "flattenAssemblies": "false", "yAxisIsUp": "false", "storeInDocument": "true"}
            r = c.post(f"/blobelements/d/{did}/w/{wid}", files=files, data=data)
        print(f"  imported {p.name} -> {r.get('id', '?')} (translation runs asynchronously)")
    print(f"\nOpen: {args.base_url}/documents/{did}/w/{wid}")
    print("Then follow SETUP.md from step 1.3 (commit studios, add features to toolbar) onward.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
