"""Zero-downtime serving: calls keep succeeding while generations roll, workers crash, or a new generation fails."""
import asyncio
import os
import signal
import sys
import textwrap
import threading
import time

import pytest

pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402

from dftracer_agents import ha  # noqa: E402

WORKER = textwrap.dedent('''
    import os, sys
    from fastmcp import FastMCP
    if os.environ.get("HA_TEST_FAIL") and os.path.exists(os.environ["HA_TEST_FAIL"]):
        sys.exit(3)                                   # a broken new generation
    port = int(sys.argv[1])
    mcp = FastMCP("t")
    @mcp.tool()
    def whoami() -> str:
        """pid and generation of the worker that answered"""
        return f"{os.getpid()}:{os.environ.get('DFTRACER_HA_GEN')}"
    import asyncio
    asyncio.run(mcp.run_http_async(transport="streamable-http", host="127.0.0.1", port=port, path="/mcp",
                                   show_banner=False, stateless_http=True))
''')


@pytest.fixture()
def sup(tmp_path, monkeypatch):
    script = tmp_path / "worker.py"
    script.write_text(WORKER)
    fail = tmp_path / "FAIL"
    monkeypatch.setenv("HA_TEST_FAIL", str(fail))
    monkeypatch.setenv("USER", "hatest")
    fp = {"v": "code-v1"}
    monkeypatch.setattr(ha, "code_fingerprint", lambda: fp["v"])
    monkeypatch.setattr(ha, "content_fingerprint", lambda: "c1")
    gw = ha.free_port()
    s = ha.Supervisor(port=gw, host="127.0.0.1", workers=2, poll=0.3, drain_s=5, ready_timeout=60,
                      worker_cmd=lambda port: [sys.executable, str(script), str(port)], content_hook=lambda: None)
    s.log = lambda *a, **k: None
    t = threading.Thread(target=s.serve, daemon=True)
    t.start()
    for _ in range(300):
        if s.pool.active and s._srv:
            break
        time.sleep(0.1)
    assert s.pool.active, "supervisor did not start"
    s.fp, s.url, s.fail = fp, f"http://127.0.0.1:{gw}/mcp", fail
    yield s
    s.stop.set()
    t.join(20)


async def _call(url):
    async with Client(url) as c:
        r = await c.call_tool("whoami", {})
        return r.content[0].text


def who(url):
    return asyncio.run(_call(url))


class Hammer:
    """Fires calls through the gateway in the background and records every result."""

    def __init__(self, url):
        self.url, self.ok, self.bad, self.gens, self.stop = url, 0, [], set(), threading.Event()
        self.t = threading.Thread(target=self.run, daemon=True)
        self.t.start()

    def run(self):
        while not self.stop.is_set():
            try:
                pid, gen = who(self.url).split(":")
                self.ok += 1
                self.gens.add(gen)
            except Exception as e:
                self.bad.append(repr(e)[:200])
            time.sleep(0.02)

    def finish(self):
        self.stop.set()
        self.t.join(30)


def wait_for(cond, secs=60):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def test_gateway_serves_mcp_through_streamed_replies(sup):
    pid, gen = who(sup.url).split(":")
    assert gen.startswith("g1-") and int(pid) > 0
    st = ha.read_state()
    assert st and st["generation"] == gen and len(st["workers"]) == 2


def test_code_change_rolls_to_a_new_generation_without_failed_calls(sup):
    h = Hammer(sup.url)
    assert wait_for(lambda: h.ok > 5)
    old = {w.proc.pid for w in sup.pool.active}
    sup.fp["v"] = "code-v2"                                       # the package source "changed"
    assert wait_for(lambda: sup.swaps >= 2 and not sup.pool.backup, 90)
    time.sleep(0.5)
    h.finish()
    assert h.bad == [] and h.ok > 20                              # not one request failed during the swap
    assert len({g for g in h.gens}) >= 2                          # both generations answered at some point
    assert wait_for(lambda: all(not (w_pid_alive(p)) for p in old), 30)   # old generation was stopped
    assert ha.read_state()["generation"].startswith("g2-") and ha.read_state()["source_current"]


def w_pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def test_failed_new_generation_keeps_serving_the_old_one(sup):
    h = Hammer(sup.url)
    assert wait_for(lambda: h.ok > 3)
    gen1 = sup.pool.active[0].gen
    sup.fail.write_text("x")                                      # new workers will exit immediately
    sup.fp["v"] = "code-broken"
    assert wait_for(lambda: sup.last_error and "failed" in sup.last_error, 60)
    time.sleep(1.0)
    h.finish()
    assert h.bad == [] and sup.pool.active[0].gen == gen1         # backup (old generation) never stopped serving
    swaps = sup.swaps
    time.sleep(1.5)
    assert sup.swaps == swaps                                     # the same broken source is not retried in a loop
    sup.fail.unlink()
    sup.fp["v"] = "code-fixed"                                    # a new edit is picked up again
    assert wait_for(lambda: sup.swaps > swaps and sup.last_error is None, 90)
    assert who(sup.url)


def test_crashed_worker_is_replaced_and_requests_survive(sup):
    h = Hammer(sup.url)
    assert wait_for(lambda: h.ok > 3)
    victim = sup.pool.active[0]
    os.kill(victim.proc.pid, signal.SIGKILL)
    assert wait_for(lambda: victim not in sup.pool.active and all(w.alive for w in sup.pool.active), 60)
    h.finish()
    assert h.bad == [], h.bad[:3]
    assert len(sup.pool.active) == 2


def test_sighup_style_reload_forces_a_rolling_restart(sup):
    before = sup.pool.active[0].gen
    sup.reload_now.set()
    assert wait_for(lambda: sup.pool.active[0].gen != before, 60)
    assert who(sup.url)
