"""Card watcher: start once per card, only when the card is stable, one run at a time, never on empty cards."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from card_watcher import Watcher  # noqa: E402


def card(root: Path, files: dict):
    (root / "DCIM" / "100GOPRO").mkdir(parents=True, exist_ok=True)
    for name, size in files.items():
        (root / "DCIM" / "100GOPRO" / name).write_bytes(b"\0" * size)


def test_watcher_lifecycle(tmp_path):
    mounted = []
    w = Watcher(tmp_path / "state.json", tmp_path / "w.log", roots_fn=lambda: list(mounted), dry_run=True)
    a = tmp_path / "E"
    card(a, {"GX010001.MP4": 100})
    mounted.append(a)
    w.poll()
    assert w.started == []                       # first sighting: wait for a stable file list
    card(a, {"GX010002.MP4": 50})                # still being written / mounted
    w.poll()
    assert w.started == []
    w.poll()                                     # unchanged for two polls -> start
    assert len(w.started) == 1 and "--overnight" in w.started[0] and "_E" in " ".join(w.started[0])
    w.poll(); w.poll()
    assert len(w.started) == 1                   # same card never runs twice
    mounted.clear(); w.poll(); mounted.append(a); w.poll(); w.poll()
    assert len(w.started) == 1                   # re-inserted, same contents: ignored
    b = tmp_path / "F"
    card(b, {"DJI_0001.MP4": 10})
    (tmp_path / "G" / "DCIM").mkdir(parents=True)   # empty card
    mounted += [b, tmp_path / "G"]
    w.poll(); w.poll()
    assert len(w.started) == 2 and "_F" in " ".join(w.started[1])   # new card runs; the empty one never does
    w2 = Watcher(tmp_path / "state.json", tmp_path / "w.log", roots_fn=lambda: [a], dry_run=True)
    w2.poll(); w2.poll()
    assert w2.started == []                      # remembered across restarts (state file)
