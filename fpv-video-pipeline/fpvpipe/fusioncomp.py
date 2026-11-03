"""Make Fusion comps built by script survive a project save / reload.

Resolve 21.1 keeps a comp made with TimelineItem.AddFusionComp() (and every tool added to it through the API) in
memory only: SaveProject() returns True, but after LoadProject() the clip has no comp at all - Gyroflow, reframe pans
and speed ramps vanish. A comp that went through ExportFusionComp() / ImportFusionComp() is stored properly (tested
2026-11-03: of three identical comps, only the re-imported one came back). So the builder calls persist() on every
clip after all Fusion work, and again after any later API change to a comp.
"""
from __future__ import annotations

import tempfile
from pathlib import Path


def persist(item, scratch: Path | None = None) -> bool:
    """Round-trip the clip's first Fusion comp through a .comp file so it is written into the project."""
    n = item.GetFusionCompCount() or 0
    if not n:
        return True
    folder = Path(scratch or tempfile.gettempdir()) / "fpv_fusion_persist"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{abs(hash(item.GetUniqueId() if hasattr(item, 'GetUniqueId') else item.GetName()))}.comp"
    if not item.ExportFusionComp(str(path), 1):
        return False
    before = list(item.GetFusionCompNameList() or [])
    item.DeleteFusionCompByName(before[0])                  # returns False in 21.1 even when it worked; count tells
    if not item.ImportFusionComp(str(path)):
        return False
    after = list(item.GetFusionCompNameList() or [])
    for name in after[:-1] if len(after) > 1 else []:       # import added a second comp instead of replacing
        item.DeleteFusionCompByName(name)
    return (item.GetFusionCompCount() or 0) >= 1


def persist_track(tl, track: int = 1, scratch: Path | None = None) -> list[str]:
    """persist() every clip on a video track; returns the names of clips whose comp could not be saved."""
    return [it.GetName() for it in (tl.GetItemListInTrack("video", track) or []) if not persist(it, scratch)]
