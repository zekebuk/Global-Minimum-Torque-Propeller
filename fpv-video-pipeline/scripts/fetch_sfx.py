#!/usr/bin/env python3
"""Download the generated ElevenLabs SFX library into <SFX>/<category>/<name>_<n>.mp3 (desktop, one time).

    python scripts/fetch_sfx.py --links C:\\Users\\<you>\\Downloads\\sfx_links.json

assets/sfx_manifest.json (in the repo) lists every sound: category/name, prompt, length, loop, generation ids.
It has no download links because the repo is public. The links come in a private sfx_links.json that Claude sends you.
They are signed and expire (see "expires_utc" in the file); if they have, ask Claude for fresh links.
Each file is checked (decodes, length within 10% of the request) and recorded in <SFX>/library.json, the same index
make_sfx_library.py writes, so build_soundscape.py, the reel tools and Resolve's 03_SFX bin all find them.
Existing files are skipped, so a re-run only fills gaps.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, media  # noqa: E402

MANIFEST = Path(__file__).resolve().parent.parent / "assets" / "sfx_manifest.json"


def duration_s(path: Path) -> float:
    return float(media.ffprobe(path).get("format", {}).get("duration", 0.0))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--links", type=Path, required=True, help="private sfx_links.json from Claude")
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    root = cfg["paths"].get("sfx") or cfg["paths"]["music"].parent / "SFX"
    manifest = json.loads(args.manifest.read_text())
    links = json.loads(args.links.read_text())
    exp = links.get("expires_utc")
    if exp and datetime.fromisoformat(exp.replace("Z", "+00:00")) < datetime.now(timezone.utc):
        print(f"! these links expired at {exp}: ask Claude for fresh SFX links (the sounds are still in your ElevenLabs flow)")
        return 1

    index_path = root / "library.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    ok = skipped = failed = 0
    for label, snd in manifest["sounds"].items():
        for take in snd["takes"]:
            rel = take["file"]
            out = root / rel
            if out.exists() and out.stat().st_size > 0:
                skipped += 1
                continue
            url = links["links"].get(rel)
            if not url:
                print(f"! no link for {rel}")
                failed += 1
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".part")
            try:
                with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                    f.write(r.read())
                d = duration_s(tmp)
            except Exception as e:
                tmp.unlink(missing_ok=True)
                print(f"FAILED {rel}: {e}")
                failed += 1
                continue
            if d <= 0 or abs(d - snd["duration_s"]) > max(0.15, 0.1 * snd["duration_s"]):
                print(f"FAILED {rel}: length {d:.2f}s, expected {snd['duration_s']}s; kept as .part for inspection")
                failed += 1
                continue
            tmp.replace(out)
            index[rel] = {"prompt": snd["prompt"], "seconds": round(d, 2), "loop": snd["loop"],
                          "model": "eleven_text_to_sound_v2", "generated": manifest.get("generated", ""),
                          "generation_id": take["generation_id"], "source": "ElevenLabs sound effects (creative flow)"}
            ok += 1
            print(f"ok {rel} ({d:.2f}s)")
    root.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=2))
    print(f"\n{ok} downloaded, {skipped} already there, {failed} failed -> {root}")
    print("Next: build_resolve_project.py / build_reel_project.py import this folder into the SFX bin (one sub-bin per category).")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
