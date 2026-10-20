"""Self-refreshing, multi-instance snapshot store (stdlib only, no project imports).

Problem: an MCP server answers many users at once (one process per Claude session, or one shared
HTTP daemon) from derived knowledge - a parsed knowledge base, a graph, an index. When the sources
change, nobody should have to restart or "refresh" anything, and a refresh must never make the
server stop answering.

Mechanism (blue/green, eventually consistent):

* A **generation** is an immutable build of the derived data, stored in ``<cache>/gen-<fingerprint>/``
  (the fingerprint hashes path/mtime/size of every source file, so equal sources => equal id).
* Each instance serves its **active** generation. Readers never wait: ``current()`` returns the active
  generation immediately and, at most every ``interval`` seconds, starts a background check.
* If the fingerprint changed, ONE instance wins an ``O_EXCL`` build lock, builds into a temp dir,
  validates it, then publishes it with an atomic ``rename`` and repoints ``CURRENT``. Every other
  instance sees the lock, keeps serving its active generation (the **backup**), and adopts the new
  generation on a later tick without rebuilding. A crashed builder's lock expires after ``lock_ttl``.
* A build or validation failure never replaces the active generation; the error is kept in
  ``status()`` and the previous generation keeps serving. The generation before the active one is kept
  as **standby** and ``with_fallback`` retries a failing call against it.

Consistency: every instance converges to the newest sources within ``interval`` + build time.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional

INSTANCE = f"{socket.gethostname()}:{os.getpid()}"


def fingerprint_paths(roots: Iterable[Path], patterns: Iterable[str] = ("**/*",), extra: str = "") -> str:
    """Cheap content-change fingerprint: sha1 over (relative path, mtime_ns, size) of matching files."""
    h = hashlib.sha1(extra.encode())
    for root in sorted({Path(r) for r in roots}, key=str):
        if root.is_file():
            files = [root]
        elif root.is_dir():
            files = sorted({f for p in patterns for f in root.glob(p) if f.is_file()})
        else:
            continue
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            h.update(f"{f}|{st.st_mtime_ns}|{st.st_size}\n".encode())
    return h.hexdigest()[:16]


class Generation:
    __slots__ = ("id", "path", "value", "built_at", "built_by")

    def __init__(self, id: str, path: Path, value: Any, built_at: float, built_by: str):
        self.id, self.path, self.value, self.built_at, self.built_by = id, path, value, built_at, built_by


class SnapshotStore:
    def __init__(self, name: str, fingerprint: Callable[[], str], build: Callable[[Path], None],
                 load: Callable[[Path], Any], cache_dir: Path,
                 validate: Optional[Callable[[Any], List[str]]] = None,
                 interval: float = 15.0, lock_ttl: float = 300.0, lock_wait: float = 120.0,
                 bad_ttl: float = 60.0, keep: int = 3):
        self.name, self._fp, self._build, self._load = name, fingerprint, build, load
        self._validate = validate or (lambda v: [])
        self.root = Path(cache_dir) / name
        self.interval, self.lock_ttl, self.lock_wait, self.bad_ttl, self.keep = interval, lock_ttl, lock_wait, bad_ttl, keep
        self._lock = threading.Lock()          # guards pointer swaps
        self._active: Optional[Generation] = None
        self._standby: Optional[Generation] = None
        self._tick_lock = threading.Lock()     # one check/build at a time per instance
        self._last_check = 0.0
        self.last_error: Optional[str] = None
        self.swaps = 0

    # ------------------------------------------------------------------ reading
    def current(self) -> Generation:
        a = self._active
        if a is None:
            return self._first_load()
        if time.time() - self._last_check >= self.interval:
            self._kick()
        return a

    def with_fallback(self, fn: Callable[[Any], Any]) -> Any:
        """Run fn(active value); if it raises and a standby generation exists, retry on the standby."""
        a = self.current()
        try:
            return fn(a.value)
        except Exception as e:
            s = self._standby
            if s is None:
                raise
            self.last_error = f"active generation {a.id} failed ({type(e).__name__}: {e}); served from standby {s.id}"
            return fn(s.value)

    def refresh(self, force: bool = False, wait: bool = True) -> dict:
        """Check now (used by tools/tests); ``force`` rebuilds even when the fingerprint is unchanged."""
        if wait:
            self._tick(force=force)
        else:
            self._kick(force=force)
        return self.status()

    # ------------------------------------------------------------------ internals
    def _kick(self, force: bool = False) -> None:
        """Background check; skipped when one is already running (readers never queue behind it)."""
        if not self._tick_lock.acquire(blocking=False):
            return
        self._last_check = time.time()          # throttle even if the check below fails
        threading.Thread(target=self._tick_locked, args=(force,), daemon=True, name=f"refresh-{self.name}").start()

    def _tick_locked(self, force: bool) -> None:
        try:
            self._tick_body(force)
        except Exception as e:                    # never let the checker die loudly
            self.last_error = f"{type(e).__name__}: {e}"
        finally:
            self._tick_lock.release()

    def _tick(self, force: bool = False) -> None:
        """Explicit check: waits for an in-flight background check, then runs its own."""
        with self._tick_lock:
            self._tick_body(force)

    def _tick_body(self, force: bool) -> None:
        self._last_check = time.time()
        fp = self._fp()
        a = self._active
        if a is not None and a.id == fp and not force:
            return
        self._adopt(fp, force=force)

    def _gen_dir(self, fp: str) -> Path:
        return self.root / f"gen-{fp}"

    def _read_gen(self, fp: str) -> Optional[Generation]:
        d = self._gen_dir(fp)
        meta = d / "READY"
        if not meta.exists():
            return None
        info = json.loads(meta.read_text() or "{}")
        return Generation(fp, d, self._load(d), info.get("built_at", 0.0), info.get("built_by", "?"))

    def _promote(self, g: Generation) -> None:
        with self._lock:
            if self._active is not None and self._active.id != g.id:
                self._standby = self._active
            self._active = g
            self.swaps += 1
        self.last_error = None
        try:
            tmp = self.root / f".CURRENT.{os.getpid()}"
            tmp.write_text(g.id)
            os.replace(tmp, self.root / "CURRENT")
        except OSError:
            pass
        self._prune()

    def _lock_path(self) -> Path:
        return self.root / "build.lock"

    def _lock_info(self) -> Optional[dict]:
        try:
            return json.loads(self._lock_path().read_text())
        except (OSError, ValueError):
            return None

    def _lock_is_live(self, info: dict) -> bool:
        if time.time() - info.get("ts", 0) > self.lock_ttl:
            return False
        if info.get("host") == socket.gethostname():
            try:
                os.kill(int(info.get("pid", 0)), 0)
            except (OSError, ValueError):
                return False
        return True

    def _acquire(self) -> bool:
        self.root.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self._lock_path(), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
                with os.fdopen(fd, "w") as f:
                    json.dump({"host": socket.gethostname(), "pid": os.getpid(), "ts": time.time()}, f)
                return True
            except FileExistsError:
                info = self._lock_info()
                if info and self._lock_is_live(info):
                    return False
                try:
                    self._lock_path().unlink()      # stale (crashed builder): take over
                except OSError:
                    return False
        return False

    def _release(self) -> None:
        try:
            self._lock_path().unlink()
        except OSError:
            pass

    def _bad(self, fp: str) -> Optional[dict]:
        try:
            info = json.loads((self.root / f"bad-{fp}.json").read_text())
        except (OSError, ValueError):
            return None
        return info if time.time() - info.get("ts", 0) < self.bad_ttl else None

    def _adopt(self, fp: str, force: bool = False) -> bool:
        """Make generation `fp` active: reuse a published one, else build it (if we win the lock)."""
        if not force:
            g = self._read_gen(fp)
            if g is not None:
                self._promote(g)
                return True
            bad = self._bad(fp)
            if bad:
                self.last_error = f"sources {fp} failed validation {int(time.time() - bad['ts'])}s ago: {bad['errors'][:3]}"
                return False
        if not self._acquire():
            return False                              # another instance is building: keep serving the backup
        tmp = self.root / f"tmp-{fp}-{os.getpid()}"
        try:
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True)
            self._build(tmp)
            value = self._load(tmp)
            errors = list(self._validate(value))
            if errors:
                (self.root / f"bad-{fp}.json").write_text(json.dumps({"ts": time.time(), "errors": errors}))
                self.last_error = f"new generation {fp} rejected, previous kept: {errors[:3]}"
                shutil.rmtree(tmp, ignore_errors=True)
                return False
            (tmp / "READY").write_text(json.dumps({"built_at": time.time(), "built_by": INSTANCE}))
            dest = self._gen_dir(fp)
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)   # forced rebuild of the same fingerprint
            os.rename(tmp, dest)
            g = self._read_gen(fp)
            if g is not None:
                self._promote(g)
            return g is not None
        except Exception as e:
            self.last_error = f"build of {fp} failed, previous kept: {type(e).__name__}: {e}"
            shutil.rmtree(tmp, ignore_errors=True)
            return False
        finally:
            self._release()

    def _first_load(self) -> Generation:
        deadline = time.time() + self.lock_wait
        while True:
            fp = self._fp()
            with self._lock:
                if self._active is not None:
                    return self._active
            try:
                self._adopt(fp)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
            if self._active is not None:
                self._last_check = time.time()
                return self._active
            # a build by another instance may be running; fall back to the newest published generation
            try:
                cur = (self.root / "CURRENT").read_text().strip()
                g = self._read_gen(cur)
                if g is not None:
                    self._promote(g)
                    return g
            except (OSError, ValueError):
                pass
            if time.time() > deadline:
                raise RuntimeError(f"{self.name}: no generation available ({self.last_error or 'build lock busy'})")
            time.sleep(0.5)

    def _prune(self) -> None:
        keep = {g.id for g in (self._active, self._standby) if g}
        gens = sorted((p for p in self.root.glob("gen-*") if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in gens[self.keep:]:
            if p.name[4:] not in keep:
                shutil.rmtree(p, ignore_errors=True)
        for p in self.root.glob("tmp-*"):
            if time.time() - p.stat().st_mtime > self.lock_ttl:
                shutil.rmtree(p, ignore_errors=True)

    # ------------------------------------------------------------------ reporting
    def status(self) -> dict:
        a, s = self._active, self._standby
        info = self._lock_info()
        gen = lambda g: None if g is None else {"id": g.id, "built_at": g.built_at, "age_s": round(time.time() - g.built_at, 1), "built_by": g.built_by}  # noqa: E731
        return {"name": self.name, "instance": INSTANCE, "active": gen(a), "standby": gen(s),
                "sources_fingerprint": self._fp(), "up_to_date": bool(a and a.id == self._fp()),
                "last_check_age_s": round(time.time() - self._last_check, 1) if self._last_check else None,
                "interval_s": self.interval, "swaps": self.swaps, "last_error": self.last_error,
                "building_elsewhere": info if info and self._lock_is_live(info) else None,
                "cache": str(self.root)}
