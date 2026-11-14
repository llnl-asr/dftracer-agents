# Lessons: dftracer_isolation

- [L-c536cfc6] a reproduction package ran only inside the session that built it -> scripts carried the session workspace path, home path and a flux job id, and relied on the session's environment -> static lint first (no hardcoded paths/ids, every script sources the config loader, required keys in config.ini, bash -n), then a detached run from a fresh copy under env -i bash -l outside the session tree (undated, dftracer-agents isolation.py)
