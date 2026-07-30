# Migration Plan: dftracer-agents

Selected: 2026-07-30. Source: `git@github.com:llnl/dftracer-agents.git` (develop). Target: `ssh://git@czgitlab.llnl.gov:7999/dftracer/dftracer-agents.git` (repo exists on czgitlab).

## Findings

- Local checkout on `develop`, up to date with origin.
- **No `.github/workflows/`** — author fresh `.gitlab-ci.yml` (Python project: pip install + pytest; black available in dev deps).
- Docs: existing Sphinx tree (`docs/` with conf.py, requirements.txt) — Pages job builds as-is, RTD-compatible.
- Dependencies in `pyproject.toml` reference GitHub: `dftracer-utils @ git+https://github.com/llnl/dftracer-utils.git@develop` and `dftracer-analyzer @ git+https://github.com/llnl/dfanalyzer.git@develop`. **Neither is mirrored to czgitlab yet** — leave on GitHub for now; switch in place (with NOTE(gitlab-migration) comments) once those projects are migrated.
- CI runs on LC corona batch runner (1 node), same `.corona-batch` inline template as cpp-logger/brahma.

## Steps

1. [x] Fetch/pull latest `develop`
2. [x] Add + verify `gitlab` remote
3. [x] Author `.gitlab-ci.yml`: `.corona-batch` template; `test` job (python module + venv, `pip install -e ".[dev]"`, `pytest test/ -x -q`); rules push + MR
4. [x] `pages` job building existing Sphinx docs (develop + temporary gitlab-migration rule)
5. [x] Local tests: YAML parses; sphinx build succeeds; pytest smoke (note failures verbatim, don't block on env-only issues)
6. [x] Commit on `gitlab-migration`; sync `.migration/` into repo
7. [x] Push `develop`, tags, `gitlab-migration`
8. [x] Pipeline: <https://czgitlab.llnl.gov/dftracer/dftracer-agents/-/pipelines>
9. [ ] User merges after green pipeline; later: flip pyproject deps to gitlab when dftracer-utils/dfanalyzer are migrated

## CI scoping decision

- `test` job runs plain `pytest test/ -x -q`: `test/conftest.py` already skips all `@pytest.mark.slow` (network/long-build integration) tests unless `--run-slow` is passed, so the default run is the self-contained unit-style subset. No extra `-k` deselection needed.
- `pip install -e ".[dev]"` in CI pulls dftracer-utils/dfanalyzer from GitHub (git+https in pyproject.toml) — requires GitHub reachability from the corona batch runner; will be switched to czgitlab URLs once those projects migrate.

## Local test results (2026-07-30)

- `yaml.safe_load(.gitlab-ci.yml)` — OK.
- `python3 -m sphinx -b html docs <scratch>` — `build succeeded, 3 warnings.`
- pytest collect (venv, `--no-deps` editable install because sandbox cannot clone the GitHub git+https deps — `fatal: could not read Username for 'https://github.com'`): `228 tests collected, 2 errors in 0.36s`. Both errors are `ModuleNotFoundError: No module named 'dftracer_mcp_server'` chains from missing (uninstalled) deps — env-only, expected to resolve with a full `pip install -e ".[dev]"` in CI.

## Executed changes (what to undo on revert)

Purely additive; nothing existing was modified.

- `gitlab` remote: `ssh://git@czgitlab.llnl.gov:7999/dftracer/dftracer-agents.git` (remove with `git remote remove gitlab`).
- Branch `gitlab-migration` (from `develop`) with two commits: `.gitlab-ci.yml` and `.migration/` copies (REVERT.md + this plan). Delete branch locally and on czgitlab to revert.
- Pushed to gitlab: `develop`, all tags, `gitlab-migration`. Optionally archive/delete the czgitlab repo.
- `pyproject.toml` untouched (GitHub dep URLs intact). `.github/` does not exist; no GitHub-facing changes. Docs pre-existed, so Pages revert is just removing `.gitlab-ci.yml`.

## Status log

- 2026-07-30: remote added, plan created.
- 2026-07-30: `.gitlab-ci.yml` authored (test + pages on `.corona-batch`); YAML/sphinx/pytest-collect checks run; committed on `gitlab-migration`; pushed develop/tags/gitlab-migration to czgitlab.
- 2026-07-30: pyproject deps flipped in place to czgitlab ssh (dftracer-utils@develop, dfanalyzer@develop, dfdiagnoser@main) after those repos migrated; NOTE(gitlab-migration) comment records the GitHub URLs; REVERT.md updated (in-place must-fix).
