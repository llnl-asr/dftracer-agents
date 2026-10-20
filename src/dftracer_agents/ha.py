"""Zero-downtime serving for the dftracer MCP HTTP server: supervisor + streaming gateway + rolling generations.

Why: a Claude session (or many, at once) talks to ONE url. When the package code changes, or a worker
dies, the url must keep answering - and nobody should have to restart or "refresh" anything.

Design (blue/green, eventually consistent):

* The **gateway** owns the public host:port and forwards each MCP request (POST/GET/DELETE, including
  streamed SSE replies) to a worker. It round-robins over the *active* generation, retries another worker
  when one refuses the connection, and falls back to the previous generation (the **backup**) while the
  new one is not ready or is being drained.
* A **generation** is N worker processes (``--stateless``: every request is self-contained, so any worker
  of any generation can serve any request - no session is lost when generations swap).
* The **supervisor** fingerprints the package source. On a change it starts a NEW generation, waits until
  every worker answers an MCP ``initialize``, switches the gateway to it, lets in-flight requests finish,
  then stops the old generation. If the new generation fails to come up, the old one keeps serving
  (the failure is recorded; the same broken source is not retried until it changes again).
* Content that workers read at call time (skills, agents, docs) needs no restart; the supervisor only
  re-links skills/agents when that content changes.
* Crashed workers are replaced; ``SIGHUP`` (or the ``server_reload`` MCP tool) forces a rolling restart;
  ``SIGTERM`` shuts everything down gracefully. State is mirrored to ``ha-state.json`` for status/tools.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import count
from pathlib import Path
from typing import Dict, List, Optional

from dftracer_agents.selfrefresh import fingerprint_paths

PKG = Path(__file__).resolve().parent
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length"}


def runtime_dir() -> Path:
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"
    p = Path("/tmp") / user / "dftracer-agents"
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_file() -> Path:
    return runtime_dir() / "ha-state.json"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def code_fingerprint() -> str:
    return fingerprint_paths([PKG], ["**/*.py"])


def content_roots() -> List[Path]:
    return [PKG / ".agents", PKG / "mcp_tools" / "resources"] + [p for p in (PKG.parents[1] / ".agents",) if p.is_dir()]


def content_fingerprint() -> str:
    return fingerprint_paths(content_roots(), ["**/*.md", "**/*.yaml", "**/*.yml", "**/*.json"])


# ============================================================ workers
class Worker:
    def __init__(self, gen: str, port: int, proc: Optional[subprocess.Popen] = None):
        self.gen, self.port, self.proc = gen, port, proc
        self.started = time.time()

    @property
    def alive(self) -> bool:
        return self.proc is None or self.proc.poll() is None

    def info(self) -> dict:
        return {"gen": self.gen, "port": self.port, "pid": self.proc.pid if self.proc else None,
                "alive": self.alive, "age_s": round(time.time() - self.started, 1)}


def probe(port: int, path: str, timeout: float = 5.0) -> bool:
    """A worker is ready when it answers an MCP initialize (stateless mode answers without a session)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "clientInfo": {"name": "ha-probe", "version": "0"}}})
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        c.request("POST", path, body, {"Content-Type": "application/json",
                                       "Accept": "application/json, text/event-stream"})
        r = c.getresponse()
        r.read()
        c.close()
        return r.status == 200
    except Exception:
        return False


# ============================================================ gateway
class Pool:
    """Which workers serve right now. `active` first, `backup` (previous generation) as fallback."""

    def __init__(self):
        self.lock = threading.Lock()
        self.active: List[Worker] = []
        self.backup: List[Worker] = []
        self._rr = count()
        self.inflight: Dict[str, int] = {}
        self.served = 0
        self.failed = 0

    def order(self) -> List[Worker]:
        with self.lock:
            act = [w for w in self.active if w.alive]
            if act:
                k = next(self._rr) % len(act)
                act = act[k:] + act[:k]
            return act + [w for w in self.backup if w.alive]

    def swap(self, new: List[Worker]) -> List[Worker]:
        with self.lock:
            old = self.active
            self.backup, self.active = old, new
            return old

    def drop_backup(self) -> None:
        with self.lock:
            self.backup = []

    def enter(self, gen: str) -> None:
        with self.lock:
            self.inflight[gen] = self.inflight.get(gen, 0) + 1

    def leave(self, gen: str) -> None:
        with self.lock:
            self.inflight[gen] = max(0, self.inflight.get(gen, 1) - 1)

    def busy(self, gen: str) -> int:
        with self.lock:
            return self.inflight.get(gen, 0)


def make_gateway_handler(pool: Pool, status_fn):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):          # keep the daemon log quiet
            pass

        def _body(self) -> bytes:
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                out = b""
                while True:
                    n = int(self.rfile.readline().strip() or b"0", 16)
                    if n == 0:
                        self.rfile.readline()
                        return out
                    out += self.rfile.read(n)
                    self.rfile.readline()
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _reply(self, code: int, obj: dict) -> None:
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _proxy(self) -> None:
            if self.path.startswith("/_ha/status"):
                return self._reply(200, status_fn())
            body = self._body()
            hdrs = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            last = "no worker available"
            for w in pool.order():
                conn = None
                pool.enter(w.gen)
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", w.port, timeout=3600)
                    conn.request(self.command, self.path, body or None, hdrs)
                    r = conn.getresponse()
                except Exception as e:           # connection refused / reset before any byte: try the next worker
                    last = f"{type(e).__name__}: {e}"
                    pool.leave(w.gen)
                    pool.failed += 1
                    if conn:
                        conn.close()
                    continue
                try:
                    self._relay(r)
                    pool.served += 1
                finally:
                    pool.leave(w.gen)
                    conn.close()
                return
            self._reply(503, {"error": "dftracer MCP gateway: " + last})

        def _relay(self, r: http.client.HTTPResponse) -> None:
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            clen = r.getheader("Content-Length")
            if clen is not None and r.getheader("Content-Type", "").split(";")[0] != "text/event-stream":
                self.send_header("Content-Length", clen)
                self.end_headers()
                if self.command != "HEAD":
                    while True:
                        d = r.read(65536)
                        if not d:
                            break
                        self.wfile.write(d)
                return
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                while True:
                    d = r.read1(65536)
                    if not d:
                        break
                    self.wfile.write(f"{len(d):x}\r\n".encode() + d + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_OPTIONS = do_HEAD = _proxy

    return H


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256
    allow_reuse_address = True


# ============================================================ supervisor
class Supervisor:
    def __init__(self, service="both", host="0.0.0.0", port=5000, path="/mcp", workers=2, poll=2.0,
                 drain_s=30.0, ready_timeout=90.0, worker_cmd=None, content_hook=None):
        self.service, self.host, self.port, self.path = service, host, port, path
        self.n, self.poll, self.drain_s, self.ready_timeout = max(1, workers), poll, drain_s, ready_timeout
        self._worker_cmd = worker_cmd or self._default_cmd
        self._content_hook = content_hook or default_content_hook
        self.pool = Pool()
        self.gen_no = 0
        self.gen_fp: Optional[str] = None
        self.content_fp: Optional[str] = None
        self.bad_fp: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_swap: Optional[float] = None
        self.swaps = 0
        self.started = time.time()
        self.reload_now = threading.Event()
        self.stop = threading.Event()
        self._srv: Optional[_Server] = None
        self.log = print

    # ---- processes
    def _default_cmd(self, port: int) -> list:
        return [sys.executable, "-m", "dftracer_agents.mcp_server", "--_child-run", "--skip-setup", "--stateless",
                "--service", self.service, "--transport", "http", "--host", "127.0.0.1", "--port", str(port),
                "--path", self.path]

    def _spawn(self, gen: str) -> Worker:
        port = free_port()
        env = dict(os.environ, DFTRACER_HA_WORKER="1", DFTRACER_SUPERVISOR_PID=str(os.getpid()), DFTRACER_HA_GEN=gen)
        proc = subprocess.Popen(self._worker_cmd(port), stdin=subprocess.DEVNULL, env=env,
                                stdout=open(runtime_dir() / f"worker-{gen}.out", "ab"),
                                stderr=open(runtime_dir() / f"worker-{gen}.err", "ab"), start_new_session=True)
        return Worker(gen, port, proc)

    @staticmethod
    def _kill(ws: List[Worker], grace: float = 10.0) -> None:
        for w in ws:
            if w.proc and w.alive:
                w.proc.terminate()
        end = time.time() + grace
        for w in ws:
            if w.proc:
                try:
                    w.proc.wait(max(0.1, end - time.time()))
                except subprocess.TimeoutExpired:
                    w.proc.kill()

    def _start_generation(self, fp: str) -> List[Worker]:
        self.gen_no += 1
        gen = f"g{self.gen_no}-{fp[:8]}"
        ws = [self._spawn(gen) for _ in range(self.n)]
        deadline = time.time() + self.ready_timeout
        pending = list(ws)
        while pending and time.time() < deadline:
            for w in list(pending):
                if not w.alive:
                    self._kill(ws, 1)
                    raise RuntimeError(f"worker {w.gen} exited early; see {runtime_dir()}/worker-{gen}.err")
                if probe(w.port, self.path):
                    pending.remove(w)
            time.sleep(0.3)
        if pending:
            self._kill(ws, 1)
            raise RuntimeError(f"generation {gen} not ready after {self.ready_timeout:.0f}s")
        return ws

    # ---- rolling restart
    def roll(self, fp: str) -> bool:
        try:
            new = self._start_generation(fp)
        except Exception as e:
            self.last_error = f"new generation for source {fp[:8]} failed, previous keeps serving: {e}"
            self.bad_fp = fp
            self.log(f"[ha] {self.last_error}", file=sys.stderr, flush=True)
            return False
        old = self.pool.swap(new)
        self.gen_fp, self.last_swap, self.last_error, self.bad_fp = fp, time.time(), None, None
        self.swaps += 1
        self.log(f"[ha] serving {new[0].gen} ({len(new)} workers); draining {old[0].gen if old else '-'}",
                 file=sys.stderr, flush=True)
        threading.Thread(target=self._drain, args=(old,), daemon=True).start()
        self.write_state()
        return True

    def _drain(self, old: List[Worker]) -> None:
        if not old:
            return
        end = time.time() + self.drain_s
        while time.time() < end and self.pool.busy(old[0].gen) > 0:
            time.sleep(0.2)
        self._kill(old)
        with self.pool.lock:
            if self.pool.backup is old or [w.port for w in self.pool.backup] == [w.port for w in old]:
                self.pool.backup = []
        self.write_state()

    # ---- maintenance loop
    def tick(self) -> None:
        fp = code_fingerprint()
        forced = self.reload_now.is_set()
        if forced or (fp != self.gen_fp and fp != self.bad_fp):
            self.reload_now.clear()
            self.roll(fp)
        cfp = content_fingerprint()
        if cfp != self.content_fp:
            self.content_fp = cfp
            try:
                self._content_hook()
            except Exception as e:
                self.last_error = f"content refresh failed: {type(e).__name__}: {e}"
        # replace crashed workers inside the active generation (the backup keeps serving meanwhile)
        with self.pool.lock:
            act = list(self.pool.active)
        for i, w in enumerate(act):
            if not w.alive:
                try:
                    nw = self._spawn(w.gen)
                    end = time.time() + self.ready_timeout
                    while time.time() < end and nw.alive and not probe(nw.port, self.path):
                        time.sleep(0.3)
                    with self.pool.lock:
                        if w in self.pool.active:
                            self.pool.active[self.pool.active.index(w)] = nw
                except Exception as e:
                    self.last_error = f"respawn failed: {e}"
        self.write_state()

    def status(self) -> dict:
        with self.pool.lock:
            act, bak = [w.info() for w in self.pool.active], [w.info() for w in self.pool.backup]
        return {"supervisor_pid": os.getpid(), "service": self.service, "public": f"http://{self.host}:{self.port}{self.path}",
                "uptime_s": round(time.time() - self.started, 1), "generation": act[0]["gen"] if act else None,
                "source_fingerprint": self.gen_fp, "source_current": self.gen_fp == code_fingerprint(),
                "workers": act, "backup_workers": bak, "swaps": self.swaps,
                "last_swap_age_s": round(time.time() - self.last_swap, 1) if self.last_swap else None,
                "last_error": self.last_error, "requests_served": self.pool.served,
                "connect_failures_retried": self.pool.failed, "stateless": True}

    def write_state(self) -> None:
        try:
            tmp = state_file().with_suffix(".tmp")
            tmp.write_text(json.dumps(self.status()))
            os.replace(tmp, state_file())
        except OSError:
            pass

    # ---- run
    def serve(self) -> int:
        self.gen_fp = None
        fp = code_fingerprint()
        if not self.roll(fp):
            print(f"[ha] cannot start: {self.last_error}", file=sys.stderr)
            return 1
        self.content_fp = content_fingerprint()
        self._srv = _Server((self.host, self.port), make_gateway_handler(self.pool, self.status))
        threading.Thread(target=self._srv.serve_forever, daemon=True, name="gateway").start()
        if threading.current_thread() is threading.main_thread():   # signal handlers only work there (tests run it in a thread)
            signal.signal(signal.SIGHUP, lambda *_: self.reload_now.set())
            signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
            signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        print(f"[ha] gateway on {self.host}:{self.port}{self.path} -> {self.n} stateless workers", file=sys.stderr, flush=True)
        while not self.stop.wait(self.poll):
            try:
                self.tick()
            except Exception as e:                     # the supervisor itself must not die
                self.last_error = f"supervisor tick failed: {type(e).__name__}: {e}"
        self.shutdown()
        return 0

    def shutdown(self) -> None:
        if self._srv:
            self._srv.shutdown()
        with self.pool.lock:
            every = self.pool.active + self.pool.backup
        self._kill(every, 5)
        try:
            state_file().unlink()
        except OSError:
            pass


def default_content_hook() -> None:
    """Skills/agents are read at call time, so nothing restarts; only (idempotently) re-link new ones."""
    from dftracer_agents.agents import ensure_agents_setup
    from dftracer_agents.skills import ensure_setup, resolve_default_target
    root = resolve_default_target()
    for fn in (ensure_setup, ensure_agents_setup):
        fn(target_root=root, force=False)


def read_state() -> Optional[dict]:
    try:
        return json.loads(state_file().read_text())
    except (OSError, ValueError):
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="dftracer MCP zero-downtime supervisor")
    ap.add_argument("--service", default="both")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--path", default="/mcp")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args(argv)
    return Supervisor(a.service, a.host, a.port, a.path, a.workers).serve()


if __name__ == "__main__":
    sys.exit(main())
