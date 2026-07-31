#!/bin/bash
# Runs ON the allocated compute node (via
#   flux proxy <jobid> flux run -N 1 bash .gitlab/ci/docs.sh)
# and builds the Sphinx docs inside a podman python container.
#
# NOTE(gitlab-migration): the pytest job is DISABLED for this project — see
# .gitlab-ci.yml. The suite is stale w.r.t. the current tree (it never ran on
# GitHub, the project had no CI). To re-enable it:
#   * install into a repo-root venv: `python -m venv venv &&
#     ./venv/bin/pip install -e ".[dev]"` — the integration tests spawn
#     <repo>/venv/bin/python explicitly;
#   * these files need updating first (they resolve paths under the pre-src/
#     layout <repo>/dftracer-agents/mcp-tools/, and four of them import a
#     repo-root `dftracer_mcp_server` module that no longer exists):
#       test_session_refactor, test_academic_service, test_dfdiagnoser_service,
#       test_session_new_tools, test_dfanalyzer_service,
#       test_dftracer_plot_service, test_dftracer_session_service
#     (test_dfanalyzer_service also asserts "--trace-path" CLI flags that the
#     service replaced with hydra-style trace_path=...).
set -ex

PODMAN_STORE=/var/tmp/$USER/podman-root
PODMAN_RUNROOT=/var/tmp/$USER/podman-run
mkdir -p "$PODMAN_STORE" "$PODMAN_RUNROOT"
PODMAN="podman --root $PODMAN_STORE --runroot $PODMAN_RUNROOT"

# --user 0:0: container root maps to the host user under rootless podman, so
# the bind-mounted checkout stays readable even for images with a non-root USER.
$PODMAN run --rm --user 0:0 -v "$PWD:/ws" -w /ws docker.io/library/python:3.11 bash -ec '
  pip install --quiet --upgrade pip
  pip install --quiet -r docs/requirements.txt
  sphinx-build -b html docs public
'
