# Sandbox images

The sandbox is the agent's entire world: its shell, its filesystem, its browser,
its toolchain. What is missing from this image is missing from Nova.

It is built as a **chain of four layers**, each one concern. Chained tags share
layers, so the intermediate tags cost essentially no extra disk — and
`config.yaml`'s `sandbox.image` can point at whichever rung you want.

| Layer | Tag | What it adds |
|---|---|---|
| 1 | `nova-sandbox-base` | The pinned upstream image. Nothing else. |
| 2 | `nova-sandbox-tools` | pandoc, wkhtmltopdf, tesseract, psql, redis-cli, Go, Rust, uv, pnpm, Playwright, ghostscript, jq, dig, … |
| 3 | `nova-sandbox-dind` | A Docker daemon the agent can use (requires `sandbox.privileged`) |
| 4 | `nova-sandbox-android` | OpenJDK 17, Android SDK, Gradle, Kotlin |

## Build

```bash
make sandbox-image              # whole chain
make sandbox-image LAYER=tools  # stop after layer 2
```

Then point `config.yaml` at the tag you built:

```yaml
sandbox:
  image: nova-sandbox-android:latest
```

Sandbox containers are created via Docker-outside-of-Docker, so once the image
exists in the local daemon no registry push is needed. For a multi-host or
Kubernetes deployment, push the tag and reference that instead.

## The base image is pinned by digest

`Dockerfile.base` pins
`all-in-one-sandbox@sha256:742062f9…` rather than `:latest`.

This is the whole reason the chain exists. Every layer used to build straight
off the floating upstream tag, so two builds a week apart could produce two
different sandboxes with no change in this repo and nothing to point at when
behaviour drifted. To adopt a new upstream, re-pull the tag, read its digest and
change that one line deliberately:

```bash
docker pull enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
docker inspect --format '{{index .RepoDigests 0}}' enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest
```

## Reused from the build host vs downloaded

`build.sh` stages toolchains from the machine doing the build into `vendor/`
(gitignored) so the image copies them instead of downloading them. When
`vendor/` is empty — a clean checkout, CI, someone else's laptop — every step
falls back to a version-pinned download, so the image still builds anywhere and
is merely *faster* here.

**The constraint that decides this is glibc.** The build host is Ubuntu 26.04
(glibc 2.43); the image is Ubuntu 22.04 (glibc **2.35**). glibc is backward- but
not forward-compatible, so a host binary linked against 2.38 copied in here
resolves fine on `PATH` and then dies at exec with `GLIBC_2.38 not found`. Every
candidate was checked with `readelf -V` before being staged.

| Reused from host | Why it is safe |
|---|---|
| Go (whole `GOROOT`) | statically linked |
| Rust (`~/.rustup` toolchain) | rustup builds against glibc 2.17 on purpose |
| `uv` | glibc 2.17 |
| `dockerd`, `containerd`, `docker`, `docker-proxy` | need glibc 2.34 |
| `containerd-shim-runc-v2` | static |

| Downloaded | Why |
|---|---|
| pandoc, wkhtmltopdf, tesseract, psql, redis-cli | not on the host either |
| jq, btop, ninja, dig, tcpdump | on the host, but linked against glibc 2.38 |
| `runc` | the one gap in the DinD stack — host build needs 2.38 |

Two things cost nothing at all: `pnpm` comes from `corepack`, which ships with
Node 22, and Playwright drives the Chromium already in the base image
(`/usr/bin/chromium-browser`) rather than downloading its own — about 400 MB
saved and one browser in the image instead of two.

## Python: use 3.12

The base image carries three interpreters with **divergent** package sets, and
`python3` resolves to the *least* equipped of them:

| Interpreter | Packages |
|---|---|
| `python3` → 3.10 | 171 |
| 3.11 | 34 |
| **3.12** | **216** |

So `pip install X` followed by `python3.12 script.py` fails with `ImportError`,
and the reverse fails too. Treat **3.12** as canonical for agent work.

## Docker-in-Docker

Layer 3 installs a real daemon, but installing it grants nothing on its own —
the container must also run with `--privileged`, which is gated behind
`sandbox.privileged` in `config.yaml` and defaults to **off**.

Leave it off unless the agent genuinely needs to build images or run services.
A privileged container is effectively host root: anything that escapes the
sandbox reaches every other service on the machine.

`dind-entrypoint.sh` starts the daemon and then `exec`s the base image's own
`/opt/gem/run.sh`, which boots the supervisord stack the gateway talks to.
Replacing that entrypoint would take the sandbox offline, so the daemon is
wrapped around it rather than substituted for it — and a daemon that fails to
start is logged and skipped, because a sandbox without Docker is still a working
sandbox for everything else.

## /dev/shm and Chromium

Docker defaults `/dev/shm` to **64 MB**, and Chromium treats that as fatal in a
way that looks like a hang rather than an error: a page loads normally, then
`screenshot` — or any renderer work — blocks until it times out. This image
ships both a browser stack and Playwright, so it is the agent's own browsing
that breaks, not just test tooling.

`sandbox.shm_size` (default `1g`) fixes it at the container, which is the right
place: the alternative is remembering `--disable-dev-shm-usage` in every script
that ever launches a browser.

## Resource caps and deadlines

Sandbox containers ran with no memory limit, no pids limit and no deadline until
2026-08-21. On a host whose `Committed_AS` already exceeded its `CommitLimit`,
one runaway build took the entire machine down instead of just its own
container. These matter more now that the sandbox can start containers of its
own.

The limits are layered on purpose — no single one of them is sufficient:

| Limit | Config | Default | Catches |
|---|---|---|---|
| Memory | `memory_limit` | `8g` | a build that allocates until the host OOMs |
| PIDs | `pids_limit` | `2048` | fork bombs, runaway build parallelism |
| CPU | `cpu_shares` | `512` | a parallel compile starving the host — **without capping anything** |
| `/dev/shm` | `shm_size` | `1g` | Chromium hanging mid-render |
| Idle | `idle_timeout` | `3600` | a sandbox nobody is using any more |
| **Lifetime** | `max_lifetime` | unset (`21600` here) | **a sandbox that never goes idle** |

**Nothing here throttles the CPU, on purpose.** `cpu_shares` sets no ceiling: an
idle machine lets a sandbox use every core, and shares only decide who wins when
the CPU is genuinely contended — under contention something has to yield or the
whole box thrashes. The hard-quota alternative, `cpu_limit` (`--cpus`), is off by
default and should stay off: the kernel enforces it by descheduling the
container's threads once the quota is spent *within each period, even when every
other core is idle*, so long agent work stalls rather than merely slowing. It
matters more than it looks because subagents share their parent thread's
sandbox — a quota there throttles an entire fan-out, not one worker.

Which leaves the clock as the only thing that can end a task that will not end
itself. The last row is the one that is easy to leave out. `idle_timeout` only reaps
sandboxes that go *quiet*, and it deliberately exempts one hosting a live
dev-server preview — correct for an idle rule, and both are holes in a deadline.
A watch loop, a dev server, or a test that never converges keeps a container
alive indefinitely while looking perfectly healthy. `max_lifetime` refreshes
nothing and exempts nothing — and because subagents share one sandbox, it ends a
whole fan-out at once, so size it for the longest task the deployment should
ever allow.

### Does this reach the containers the agent starts?

Yes, by two different mechanisms, and it is worth being explicit because the
usual failure here is assuming a timeout on the *agent* reaches the containers
the agent launched.

**Resources** nest. Inner containers get cgroups underneath this container's own
cgroup, so `--memory`, `--cpus` and `--pids-limit` bound the *sum* of everything
the agent starts — `docker compose up` with fifteen services cannot exceed what
the one sandbox was given. `dind-entrypoint.sh` adds `--default-ulimit` on top so
a single inner container cannot exhaust that shared budget by itself.

**Lifetime** nests too, but for a different reason: destroying the sandbox tears
down its PID namespace, which takes every inner container with it. That is what
makes `max_lifetime` a real deadline rather than one that stops the agent and
leaves its containers running.

## The Android layer

Deliberately no emulator/AVD: it needs KVM acceleration a plain container does
not have, so it would install but never boot. This image assembles and builds
APKs (`./gradlew assembleDebug`), it does not run them on a virtual device.
