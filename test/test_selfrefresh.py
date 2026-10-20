"""Self-refreshing snapshot store: swap, backup serving, shared builds, lock takeover, rejected builds."""
import json
import os
import threading
import time

import pytest

from dftracer_agents.selfrefresh import SnapshotStore, fingerprint_paths


class Src:
    """A tiny 'knowledge source' + counters for how often each instance builds."""

    def __init__(self, tmp):
        self.f = tmp / "src.txt"
        self.f.write_text("v1")
        self.builds = 0
        self.fail = False
        self.invalid = False

    def fp(self):
        return fingerprint_paths([self.f], ["*"])

    def build(self, out):
        self.builds += 1
        if self.fail:
            raise RuntimeError("builder crashed")
        (out / "data.json").write_text(json.dumps({"v": self.f.read_text(), "bad": self.invalid}))

    def load(self, p):
        return json.loads((p / "data.json").read_text())

    def validate(self, v):
        return ["invalid content"] if v["bad"] else []

    def store(self, tmp, **kw):
        kw.setdefault("interval", 3600.0)   # checks only when a test calls refresh(): no background races
        return SnapshotStore("t", self.fp, self.build, self.load, tmp / "cache", validate=self.validate, **kw)

    def edit(self, text):
        self.f.write_text(text)
        t = time.time() + self.edit.__dict__.setdefault("n", 0) + 1       # force a new mtime even on coarse clocks
        self.edit.__dict__["n"] += 1
        os.utime(self.f, (t, t))


def test_first_load_builds_once_and_second_instance_adopts(tmp_path):
    a, b = Src(tmp_path), Src(tmp_path)
    sa, sb = a.store(tmp_path), b.store(tmp_path)
    assert sa.current().value["v"] == "v1"
    assert sb.current().value["v"] == "v1"
    assert a.builds == 1 and b.builds == 0          # the second server reuses the published generation


def test_change_is_picked_up_without_blocking_readers(tmp_path):
    s = Src(tmp_path)
    st = s.store(tmp_path)
    old = st.current()
    s.edit("v2")
    assert st.current().id == old.id                 # reader gets the active generation immediately...
    st.refresh()                                     # ...and the background tick swaps in the new one
    assert st.current().value["v"] == "v2" and st.status()["standby"]["id"] == old.id
    assert st.status()["up_to_date"]


def test_failed_or_invalid_build_keeps_serving_previous(tmp_path):
    s = Src(tmp_path)
    st = s.store(tmp_path)
    st.current()
    s.edit("v2")
    s.fail = True
    st.refresh()
    assert st.current().value["v"] == "v1" and "builder crashed" in st.status()["last_error"]
    s.fail, s.invalid = False, True
    s.edit("v3")
    st.refresh()
    assert st.current().value["v"] == "v1" and "rejected" in st.status()["last_error"]
    builds = s.builds
    st.refresh()                                     # known-bad sources are not rebuilt in a loop
    assert s.builds == builds
    s.invalid = False
    s.edit("v4")
    st.refresh()
    assert st.current().value["v"] == "v4" and st.status()["last_error"] is None


def test_other_instance_building_means_keep_serving_backup(tmp_path):
    a, b = Src(tmp_path), Src(tmp_path)
    sa, sb = a.store(tmp_path), b.store(tmp_path)
    sa.current()
    sb.current()
    a.edit("v2")
    sb._acquire()                                    # b holds the build lock (simulated long build)
    sa.refresh()
    assert sa.current().value["v"] == "v1" and a.builds == 1       # a did not build; it serves its backup
    assert sa.status()["building_elsewhere"] is not None
    sb._release()
    sa.refresh()
    assert sa.current().value["v"] == "v2"


def test_stale_lock_of_crashed_builder_is_taken_over(tmp_path):
    s = Src(tmp_path)
    st = s.store(tmp_path, lock_ttl=0.1)
    st.current()
    s.edit("v2")
    (st.root / "build.lock").write_text(json.dumps({"host": "other-host", "pid": 1, "ts": time.time() - 5}))
    st.refresh()
    assert st.current().value["v"] == "v2"


def test_with_fallback_uses_standby_when_new_generation_misbehaves(tmp_path):
    s = Src(tmp_path)
    st = s.store(tmp_path)
    st.current()
    s.edit("v2")
    st.refresh()

    def use(v):
        if v["v"] == "v2":
            raise ValueError("bug in new generation")
        return v["v"]
    assert st.with_fallback(use) == "v1" and "served from standby" in st.status()["last_error"]


def test_concurrent_readers_never_block_during_refresh(tmp_path):
    s = Src(tmp_path)
    slow = threading.Event()
    orig = s.build

    def slow_build(out):
        slow.wait(2)
        orig(out)
    s.build = slow_build
    st = s.store(tmp_path)
    slow.set()
    st.current()
    slow.clear()
    s.edit("v2")
    t0 = time.time()
    st.refresh(wait=False)                           # starts a slow background build
    for _ in range(50):
        assert st.current().value["v"] == "v1"       # served instantly from the active generation
    assert time.time() - t0 < 1.0
    slow.set()
    for _ in range(100):
        if st.current().value["v"] == "v2":
            break
        time.sleep(0.05)
    assert st.current().value["v"] == "v2"


def test_automatic_background_refresh_without_any_explicit_call(tmp_path):
    s = Src(tmp_path)
    st = s.store(tmp_path, interval=0.05)
    assert st.current().value["v"] == "v1"
    s.edit("v2")
    deadline = time.time() + 5
    while time.time() < deadline and st.current().value["v"] != "v2":    # readers just keep reading
        time.sleep(0.05)
    assert st.current().value["v"] == "v2" and st.status()["swaps"] == 2
