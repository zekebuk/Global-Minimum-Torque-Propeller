"""fpvpipe/fusioncomp.persist: the export / delete / import round-trip that makes script-made comps survive a
project reload, including Resolve 21.1's DeleteFusionCompByName returning False and an import that stacks."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import fusioncomp  # noqa: E402


class Item:
    def __init__(self, comps, delete_works=True, import_replaces=True):
        self.comps, self.delete_works, self.import_replaces, self.exported = list(comps), delete_works, import_replaces, []

    def GetName(self):
        return "clip"

    def GetFusionCompCount(self):
        return len(self.comps)

    def GetFusionCompNameList(self):
        return list(self.comps)

    def ExportFusionComp(self, path, idx):
        self.exported.append(path)
        return idx <= len(self.comps)

    def DeleteFusionCompByName(self, name):
        if name in self.comps and (self.delete_works or True):
            self.comps.remove(name)
        return self.delete_works                    # 21.1 says False even when it removed the comp

    def ImportFusionComp(self, path):
        if not self.import_replaces and self.comps:
            self.comps.append("Imported")
        else:
            self.comps = ["Imported"]
        return True


def test_round_trip_leaves_one_comp(tmp_path):
    it = Item(["Composition1"], delete_works=False)
    assert fusioncomp.persist(it, tmp_path) and it.comps == ["Imported"]
    assert it.exported and it.exported[0].endswith(".comp") and Path(it.exported[0]).parent == tmp_path / "fpv_fusion_persist"


def test_stacked_import_is_trimmed(tmp_path):
    it = Item(["Composition1"], delete_works=False, import_replaces=False)
    real_delete, calls = it.DeleteFusionCompByName, []

    def flaky_delete(name):                       # the first delete does nothing, so the import stacks a second comp
        calls.append(name)
        return False if len(calls) == 1 else real_delete(name)
    it.DeleteFusionCompByName = flaky_delete
    assert fusioncomp.persist(it, tmp_path)
    assert it.comps == ["Imported"] and calls == ["Composition1", "Composition1"]


def test_clip_without_comp_is_fine(tmp_path):
    assert fusioncomp.persist(Item([]), tmp_path)


def test_track_reports_failures(tmp_path):
    bad = Item(["Composition1"])
    bad.ExportFusionComp = lambda path, idx: False

    class TL:
        def GetItemListInTrack(self, kind, track):
            return [Item(["Composition1"]), bad]

    assert fusioncomp.persist_track(TL(), 1, tmp_path) == ["clip"]
