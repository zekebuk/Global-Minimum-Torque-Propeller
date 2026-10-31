"""YouTube metadata helpers: chapters, description, tags. Pure functions (tested offline)."""
from __future__ import annotations

import re

# YouTube's chapter rules: first chapter at 0:00, at least 3 chapters, each at least 10 seconds long.
MIN_CHAPTERS = 3
MIN_CHAPTER_S = 10.0


def fmt_ts(seconds: float, force_hours: bool = False) -> str:
    s = int(seconds)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h or force_hours else f"{m}:{sec:02d}"


def parse_chapter_file(text: str) -> list[tuple[float, str]]:
    """Lines like '0:00 Intro' or '1:02:03 - Ridge line'."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*[-–:]?\s*(.+?)\s*$", line)
        if m:
            h, mi, s, title = int(m.group(1) or 0), int(m.group(2)), int(m.group(3)), m.group(4)
            out.append((h * 3600 + mi * 60 + s, title))
    return out


def fix_chapters(chapters: list[tuple[float, str]], duration_s: float) -> tuple[list[tuple[float, str]], list[str]]:
    """Sort, force a 0:00 start, merge chapters shorter than 10 s. Returns (chapters, problems)."""
    notes = []
    ch = sorted((max(0.0, t), name.strip()) for t, name in chapters if name.strip())
    if ch and 0 < ch[0][0] < MIN_CHAPTER_S:
        notes.append(f"moved '{ch[0][1]}' from {fmt_ts(ch[0][0])} to 0:00 (first chapter must start at 0:00)")
        ch[0] = (0.0, ch[0][1])
    elif not ch or ch[0][0] > 0:
        ch.insert(0, (0.0, "Intro"))
        notes.append("added 0:00 Intro (YouTube requires the first chapter at 0:00)")
    merged = [ch[0]]
    for t, name in ch[1:]:
        if t - merged[-1][0] < MIN_CHAPTER_S:
            notes.append(f"dropped '{name}' at {fmt_ts(t)}: under {MIN_CHAPTER_S:.0f}s after the previous chapter")
            continue
        merged.append((t, name))
    if duration_s and duration_s - merged[-1][0] < MIN_CHAPTER_S and len(merged) > 1:
        notes.append(f"dropped last chapter '{merged[-1][1]}': under {MIN_CHAPTER_S:.0f}s before the end")
        merged.pop()
    if len(merged) < MIN_CHAPTERS:
        notes.append(f"only {len(merged)} chapter(s): YouTube needs {MIN_CHAPTERS}+, so none will show")
    return merged, notes


def description(meta: dict, chapters: list[tuple[float, str]], duration_s: float, music_credits: list[str]) -> str:
    hours = duration_s >= 3600
    parts = [meta.get("description_intro", "").strip()]
    if len(chapters) >= MIN_CHAPTERS:
        parts.append("\n".join(f"{fmt_ts(t, hours)} {name}" for t, name in chapters))
    if meta.get("gear"):
        parts.append("Gear\n" + "\n".join(f"- {g}" for g in meta["gear"]))
    if music_credits:
        parts.append("Music\n" + "\n".join(f"- {c}" for c in music_credits))
    if meta.get("hashtags"):
        parts.append(" ".join(h if h.startswith("#") else f"#{h}" for h in meta["hashtags"][:15]))
    text = "\n\n".join(p for p in parts if p)
    return text[:5000]  # YouTube description limit


def tags(meta: dict, extra: list[str]) -> list[str]:
    out, total = [], 0
    for t in list(meta.get("tags", [])) + extra:
        t = t.strip()
        if t and t.lower() not in {x.lower() for x in out} and total + len(t) + 2 <= 500:  # 500-char tag budget
            out.append(t)
            total += len(t) + 2
    return out
