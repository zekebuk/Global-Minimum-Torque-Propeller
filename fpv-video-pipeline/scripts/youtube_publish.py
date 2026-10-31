#!/usr/bin/env python3
"""Step 7 - YouTube: build the upload package (title, description with chapters, tags, thumbnail), optionally upload.

    python scripts/youtube_publish.py D:\\FPV\\Exports\\FPV_quarry\\YT_final.mp4 --title "Cinematic FPV | Quarry"
        --chapters-from-resolve YT_final --thumb-at 42.5 --music-used D:\\FPV\\Music\\yt_100bpm_x.mp3
    ... add --upload [--publish-at 2026-11-08T17:00:00Z] to send it to YouTube as a scheduled/private video

Chapters come from Resolve timeline markers of one color ([youtube_meta].chapter_marker_color, default Cream):
drop a marker where each location starts and type its name. Or pass --chapters chapters.txt ("0:00 Intro" lines).
Rules are enforced (first at 0:00, 3+ chapters, 10 s minimum) and fixes are reported.
Always writes <video>.youtube.txt (copy/paste into YouTube Studio) and <video>.thumb.jpg.

--upload needs: pip install google-api-python-client google-auth-oauthlib, an OAuth client (Desktop app) JSON from
Google Cloud Console with the YouTube Data API v3 enabled, saved where [youtube_meta].client_secrets points.
IMPORTANT: projects created after 2020-07-28 upload as PRIVATE-only until the project passes YouTube's API audit.
Until then use --upload to stage a private draft, or upload the file by hand with the .youtube.txt package.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, media, youtube  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]


def chapters_from_resolve(timeline_name: str, color: str) -> list[tuple[float, str]]:
    from fpvpipe import resolve as rlib
    project = rlib.connect().GetProjectManager().GetCurrentProject()
    tl = next((project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)
               if project.GetTimelineByIndex(i).GetName() == timeline_name), None)
    if not tl:
        raise SystemExit(f"timeline {timeline_name!r} not found")
    fps = float(tl.GetSetting("timelineFrameRate") or 30)
    return [(frame / fps, m.get("name") or m.get("note") or "Chapter")
            for frame, m in sorted(tl.GetMarkers().items()) if m.get("color") == color]


def make_thumbnail(video: Path, at: float, out: Path):
    # 1280x720 JPEG under 2 MB, YouTube's thumbnail spec. Pick a frame with the subject large and sharp.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1",
                    "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720", "-q:v", "3", str(out)],
                   check=True)


def upload(video: Path, body: dict, thumb: Path | None, secrets: Path, token: Path) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        raise SystemExit("pip install google-api-python-client google-auth-oauthlib")
    creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not secrets.exists():
                raise SystemExit(f"OAuth client file missing: {secrets} (see docstring)")
            creds = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES).run_local_server(port=0)
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text(creds.to_json())
    yt = build("youtube", "v3", credentials=creds)
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(video), chunksize=64 << 20, resumable=True))
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            print(f"\ruploading {status.progress():.0%}", end="", flush=True)
    vid = resp["id"]
    print(f"\nuploaded: https://youtu.be/{vid}  (status: {resp['status'].get('privacyStatus')})")
    if thumb:
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb))).execute()
            print("thumbnail set")
        except Exception as e:  # custom thumbnails need a phone-verified channel
            print(f"thumbnail not set ({e}); upload it in Studio")
    return vid


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--title", required=True)
    ap.add_argument("--chapters", type=Path, help="text file of '0:00 Name' lines")
    ap.add_argument("--chapters-from-resolve", metavar="TIMELINE")
    ap.add_argument("--thumb-at", type=float, help="seconds into the video for the thumbnail frame")
    ap.add_argument("--thumbnail", type=Path, help="use this image instead of extracting a frame")
    ap.add_argument("--music-used", type=Path, nargs="*", default=[], help="tracks used (credits from .license.json)")
    ap.add_argument("--tags", nargs="*", default=[])
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
    ap.add_argument("--publish-at", help="RFC3339 UTC time to go public (forces private until then)")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    meta = cfg.get("youtube_meta", {})

    if len(args.title) > 100:
        raise SystemExit(f"title is {len(args.title)} chars; YouTube allows 100")
    dur = media.video_summary(media.ffprobe(args.video))["duration_s"]
    raw = []
    if args.chapters:
        raw = youtube.parse_chapter_file(args.chapters.read_text())
    elif args.chapters_from_resolve:
        raw = chapters_from_resolve(args.chapters_from_resolve, meta.get("chapter_marker_color", "Cream"))
    chapters, notes = youtube.fix_chapters(raw, dur) if raw else ([], [])
    for n in notes:
        print(f"  chapters: {n}")

    credits = []
    for m in args.music_used:
        lic = m.with_suffix(".license.json")
        credits.append(f"{m.stem} - generated with {json.loads(lic.read_text())['source']}" if lic.exists() else m.stem)
    desc = youtube.description(meta, chapters, dur, credits)
    tag_list = youtube.tags(meta, args.tags)

    thumb = args.thumbnail
    if not thumb and args.thumb_at is not None:
        thumb = args.video.with_suffix(".thumb.jpg")
        make_thumbnail(args.video, args.thumb_at, thumb)
        print(f"thumbnail: {thumb}")

    pkg = args.video.with_suffix(".youtube.txt")
    pkg.write_text(f"TITLE\n{args.title}\n\nDESCRIPTION\n{desc}\n\nTAGS\n{', '.join(tag_list)}\n")
    print(f"upload package: {pkg}")
    print("\n" + desc[:600] + ("..." if len(desc) > 600 else ""))

    if args.upload:
        status = {"privacyStatus": "private" if args.publish_at else args.privacy, "selfDeclaredMadeForKids": False}
        if args.publish_at:
            status["publishAt"] = args.publish_at
        body = {"snippet": {"title": args.title, "description": desc, "tags": tag_list,
                            "categoryId": str(meta.get("category_id", 19))}, "status": status}
        root = Path(__file__).resolve().parent.parent
        upload(args.video, body, thumb, root / meta.get("client_secrets", "secrets/youtube_client.json"),
               root / meta.get("token_file", "secrets/youtube_token.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
