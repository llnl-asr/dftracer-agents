---
name: bug-dftracer-stale-brahma-after-pip-uninstall
description: pip uninstall dftracer leaves its CMake-installed brahma tree behind; the next develop build compiles against the stale headers and fails with "only virtual member functions can be marked 'override'"
metadata:
  type: feedback
---

Upgrading an existing session venv's dftracer to a newer `develop` fails at
compile time with a wall of:

```
src/dftracer/core/brahma/posix.h:231: error: only virtual member functions can be marked 'override'
  ssize_t readv(int fd, const struct iovec* iov, int iovcnt) override;
```

**Why:** `pip uninstall dftracer` removes only the files pip recorded. The
dftracer build ALSO cmake-installs its C dependencies into the package prefix
(`<venv>/lib/pythonX.Y/site-packages/dftracer/{include,lib64,bin,etc,share}`),
and those are left behind. dftracer's `setup.py` then passes
`-DCMAKE_PREFIX_PATH=<that same prefix>`, so the new build compiles against the
OLD `brahma/interface/posix.h`. The newer dftracer overrides
`readv`/`writev`/`pread64`/... which the stale brahma base class never declared.
Nothing in the error mentions a stale dependency, so it reads like a
compiler/toolchain problem and sends you down the wrong path.

**How to apply:** before reinstalling or upgrading dftracer into an existing
venv, delete the leftover cmake tree explicitly:

```bash
rm -rf <venv>/lib/pythonX.Y/site-packages/dftracer <venv>/bin/dftracer_service
pip uninstall -y dftracer pydftracer dftracer-utils
```

Confirm the diagnosis first — on the stale copy,
`grep -c readv <venv>/.../site-packages/dftracer/include/brahma/interface/posix.h`
returns 0. See [[tools-dftracer]], [[tools-pydftracer]].
