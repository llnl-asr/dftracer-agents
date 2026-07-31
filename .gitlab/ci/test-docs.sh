#!/bin/bash
# Runs ON the allocated compute node (via
#   flux proxy <jobid> flux run -N 1 bash .gitlab/ci/test-docs.sh)
# and executes the CI inside a podman python container.
set -ex

PODMAN_STORE=/var/tmp/$USER/podman-root
PODMAN_RUNROOT=/var/tmp/$USER/podman-run
mkdir -p "$PODMAN_STORE" "$PODMAN_RUNROOT"
PODMAN="podman --root $PODMAN_STORE --runroot $PODMAN_RUNROOT"

# pyproject pulls dftracer-utils/dfanalyzer/dfdiagnoser over czgitlab ssh, so
# the container needs the runner account's ssh keys (read-only mount).
$PODMAN run --rm -v "$PWD:/ws" -w /ws \
  -v "$HOME/.ssh:/root/.ssh:ro" \
  -e GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=no" \
  docker.io/library/python:3.11 bash -ec '
  apt-get -o APT::Sandbox::User=root update -qq
  apt-get -o APT::Sandbox::User=root install -y -qq openssh-client git
  pip install --quiet --upgrade pip
  pip install -e ".[dev]"
  pytest test/ -x -q
'

$PODMAN run --rm -v "$PWD:/ws" -w /ws docker.io/library/python:3.11 bash -ec '
  pip install --quiet --upgrade pip
  pip install --quiet -r docs/requirements.txt
  sphinx-build -b html docs public
'
