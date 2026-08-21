#!/usr/bin/env bash
# Start a Docker daemon inside the sandbox, then hand off to the base image's
# own entrypoint.
#
# The base image boots a full supervisord stack via /opt/gem/run.sh (the HTTP
# API the gateway talks to, the VNC/browser stack, and more). Replacing that
# entrypoint would take the sandbox offline, so this wraps it: bring up dockerd
# in the background, then `exec` the original as PID 1's successor so signals
# and exit codes still behave.
#
# Failure here is deliberately non-fatal. A sandbox that cannot start dockerd is
# still a working sandbox for every non-container task, and taking the whole
# agent offline because a nested daemon failed would be a much worse outcome
# than `docker: command failed`.
set -uo pipefail

readonly ORIGINAL_ENTRYPOINT=/opt/gem/run.sh
readonly DOCKERD_LOG=/var/log/dind.log

start_dockerd() {
    if [ ! -w /proc/sys ]; then
        echo "dind: /proc/sys is read-only — container is not privileged; skipping dockerd" >&2
        return 1
    fi

    mkdir -p /var/lib/docker /var/log

    # An inner daemon on its own address space. The default bridge subnet would
    # be a coin-flip against the host's, and a collision silently breaks either
    # the inner containers' networking or the sandbox's own route to the gateway.
    #
    # On the inner daemon's own limits. The common hole in an agent sandbox is
    # assuming a timeout on the agent process reaches the containers the agent
    # started. Two things close it here, and they are different mechanisms:
    #
    #   Resources — inner containers get cgroups nested *under* this container's
    #   own cgroup, so the outer --memory / --cpus / --pids-limit already bound
    #   the sum of everything the agent starts. `docker compose up` with fifteen
    #   services cannot exceed what this one container was given. The
    #   --default-ulimit below adds a per-inner-container floor on top of that,
    #   so one inner container cannot exhaust the shared budget by itself.
    #
    #   Lifetime — killing this container tears down its PID namespace, which
    #   takes every inner container with it. That is what makes
    #   `sandbox.max_lifetime` a real deadline rather than one that only stops
    #   the agent and leaves its containers running.
    dockerd \
        --host=unix:///var/run/docker.sock \
        --data-root=/var/lib/docker \
        --bridge=none \
        --iptables=true \
        --default-address-pool base=172.31.0.0/16,size=24 \
        --default-ulimit nproc=512:512 \
        --default-ulimit nofile=1024:4096 \
        >>"$DOCKERD_LOG" 2>&1 &

    for _ in $(seq 1 30); do
        if docker info >/dev/null 2>&1; then
            echo "dind: dockerd ready" >&2
            return 0
        fi
        sleep 1
    done

    echo "dind: dockerd did not become ready in 30s; see $DOCKERD_LOG" >&2
    return 1
}

start_dockerd || true

exec "$ORIGINAL_ENTRYPOINT" "$@"
