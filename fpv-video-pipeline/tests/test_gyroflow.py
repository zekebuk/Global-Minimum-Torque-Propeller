"""Offline tests for fpvpipe/gyroflow.py with stand-in Resolve / Fusion objects. No Resolve needed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import gyroflow  # noqa: E402


class Tool:
    def __init__(self, reg, comp):
        self.reg, self.comp, self.inputs = reg, comp, {}
        self.Output = f"{reg}.Output"

    def GetAttrs(self):
        return {"TOOLS_RegID": self.reg}

    def Comp(self):
        return self.comp

    def SetInput(self, k, v):
        self.inputs[k] = v

    def __setattr__(self, k, v):
        if k[:1].isupper() and k != "Output":
            self.__dict__.setdefault("inputs", {})[k] = v
        object.__setattr__(self, k, v)


class Comp:
    def __init__(self):
        self.tools = [Tool("MediaIn", self), Tool("MediaOut", self)]

    def GetToolList(self, _sel):
        return {i + 1: t for i, t in enumerate(self.tools)}

    def AddTool(self, reg, _x, _y):
        self.tools.append(Tool(reg, self))
        return self.tools[-1]

    def Lock(self):
        pass

    Unlock = Lock


class MediaPoolItem:
    def __init__(self, path, res="3840x3360"):
        self.props = {"File Path": path, "Resolution": res}

    def GetClipProperty(self, k):
        return self.props[k]


class Item:
    def __init__(self, path, speed=100.0, comps=0):
        self.mpi, self.speed = MediaPoolItem(path), speed
        self.comps = [Comp() for _ in range(comps)]

    def GetName(self):
        return Path(self.mpi.props["File Path"]).name

    def GetMediaPoolItem(self):
        return self.mpi

    def GetSpeed(self):
        return {"Percentage": self.speed}

    def GetFusionCompCount(self):
        return len(self.comps)

    def GetFusionCompByIndex(self, i):
        return self.comps[i - 1]

    def GetFusionCompNameList(self):
        return [f"Composition {i + 1}" for i in range(len(self.comps))]

    def DeleteFusionCompByName(self, name):
        self.comps.pop(int(name.split()[-1]) - 1)
        return True

    def AddFusionComp(self):
        self.comps.append(Comp())
        return self.comps[-1]


class Timeline:
    def __init__(self, items):
        self.items = items

    def GetItemListInTrack(self, _kind, _track):
        return self.items


def test_preset_merges_config_over_defaults():
    cfg = {"gyroflow": {"mode": "ofx", "reel": {"smoothness": 40}}}
    assert gyroflow.mode(cfg) == "ofx"
    assert gyroflow.preset(cfg, "reel") == {**gyroflow.DEFAULTS, "smoothness": 40}
    assert gyroflow.preset({}, "youtube") == gyroflow.DEFAULTS
    assert gyroflow.mode({}) == "intermediates"


def test_apply_wires_plugin_and_sets_size_speed_settings(monkeypatch):
    monkeypatch.setattr(gyroflow, "has_gyro", lambda p: True)
    it = Item(r"E:\RAW\s\raw\GX010042.MP4", speed=125.0)
    settings = gyroflow.preset({}, "reel")
    assert gyroflow.apply(it, settings) == "added"
    gf = gyroflow.find(it)
    mi, mo = it.comps[0].tools[0], it.comps[0].tools[1]
    assert gf.inputs["Source"] == mi.Output and mo.inputs["Input"] == gf.Output
    assert gf.inputs["ProjectPath"] == r"E:\RAW\s\raw\GX010042.MP4"
    assert (gf.inputs["OutputWidth"], gf.inputs["OutputHeight"]) == (3840.0, 3360.0)
    assert gf.inputs["VideoSpeed"] == 125.0
    assert gf.inputs["Smoothness"] == settings["smoothness"]
    # re-running updates in place instead of stacking a second comp
    assert gyroflow.apply(it, {**settings, "smoothness": 80}) == "updated"
    assert it.GetFusionCompCount() == 1 and gf.inputs["Smoothness"] == 80.0


def test_apply_skips_stabilized_and_hand_made_comps(monkeypatch):
    monkeypatch.setattr(gyroflow, "has_gyro", lambda p: "stab" not in str(p))
    tl = Timeline([Item("a_stabilized.mp4"), Item("b.MP4", comps=1), Item("c.MP4"), Item("c.MP4")])
    res = gyroflow.stabilize_timeline(tl, gyroflow.DEFAULTS)
    assert res == {"no gyro": ["a_stabilized.mp4"], "has comp": ["b.MP4"], "added": ["c.MP4", "c.MP4"]}


def test_per_shot_override(monkeypatch):
    monkeypatch.setattr(gyroflow, "has_gyro", lambda p: True)
    tl = Timeline([Item("a.MP4"), Item("b.MP4")])
    gyroflow.stabilize_timeline(tl, {**gyroflow.DEFAULTS, "smoothness": 25}, overrides=[{}, {"smoothness": 35}])
    assert [gyroflow.find(it).inputs["Smoothness"] for it in tl.items] == [25.0, 35.0]


def test_remove_only_deletes_gyroflow_comps(monkeypatch):
    monkeypatch.setattr(gyroflow, "has_gyro", lambda p: True)
    it = Item("c.MP4")
    gyroflow.apply(it, gyroflow.DEFAULTS)
    assert gyroflow.remove(it) and it.GetFusionCompCount() == 0
    own = Item("d.MP4", comps=1)
    own.comps[0].AddTool(gyroflow.PLUGIN_ID, 0, 0)
    own.comps[0].AddTool("Transform", 0, 0)           # the user's own work in the same comp: keep it
    assert not gyroflow.remove(own) and own.GetFusionCompCount() == 1
