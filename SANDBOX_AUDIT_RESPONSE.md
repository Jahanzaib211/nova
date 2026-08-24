# Response to the in-sandbox self-probe (2026-08-22)

An agent running **inside a spawned Nova sandbox** produced a self-probe concluding that Nova is
"OpenHands-derived, not a DeerFlow fork", that the Nova/Ali branding is "not substantiated by any
file on disk", and that the `apex-seo-platform` skill is "VAPORWARE".

It audited the disposable container Nova *spawns* and inferred the identity of the system that
*built* it. Nova's source runs on the host, in the gateway; it never enters the sandbox.

Of 20 claims examined: **10 overturned, 6 upheld, 4 partly upheld.** The split is not random — it
was a good toolchain auditor and a poor architecture auditor.

Full ledger with evidence: https://claude.ai/code/artifact/5513bcb1-1f7f-4091-85ec-2760f44f7f51

---

## Why it went wrong

The sandbox image is a four-rung chain. `/opt` — where the probe looked — belongs entirely to the
bottom rung, a third-party base image pinned by digest and never written to.

| Rung | Owner | Contents |
|---|---|---|
| `android` | Nova | JDK 17, Gradle 8.7, Kotlin, Android SDK 34 |
| `dind` | Nova | dockerd 29.6.2, `dind-entrypoint.sh` (overrides upstream's entrypoint) |
| `tools` | Nova | Go, Rust, Playwright, LibreOffice, kubectl, poppler … |
| `base` | **Upstream** | `all-in-one-sandbox@sha256:742062f9…` — ships `/opt`, `/etc/aio_version`, vendored OpenHands |

`grep -ri openhands` across the whole repo returns **zero matches**. Nova neither forks nor vendors
OpenHands; it consumes the vendor image over HTTP.

## Overturned (selected)

- **"Not a fork / OpenHands-derived."** Every cited artifact resolves to an 8-month-old upstream
  layer. `docker/sandbox/Dockerfile.base:19` pins the digest.
- **"`NOVA_SANDBOX_BUILD` is a build ID, not an identity."** `build.sh:175` stamps
  `$(git rev-parse --short HEAD)-<UTC>`. The running stamp `8fa6abaa-20260822T002104Z` resolves to
  a real commit dated 00:14:39 UTC — 6m25s before the build. One command would have settled it.
- **"D-in-D not supported — no socket."** D-in-D deliberately does *not* mount a host socket; that
  is DooD, reserved for the gateway (`docker-compose.dood.yaml`). The image ships dockerd 29.6.2.
- **"Kernel 7.0.0-29 is anomalous."** Containers share the host kernel. Host is Ubuntu 26.04 LTS.
- **"Zero matches for alilabsx / Apex."** Zero in `/opt`, which is upstream's. In the repo:
  alilabsx 35 files, Ali Technologies 19, Jahanzaib211 27, "Agent's Computer" 67.
- **"crawl4ai missing."** It is a healthy digest-pinned service on :11235 (v0.9.2), wired at
  `config.yaml:104,118` — and the probe's own tests #3/#4 passed *using* it.
- **"apex-seo-platform is vaporware."** Live PM2 service, 17 days uptime, 0 restarts, port 3002,
  backed by Postgres+pgvector / Ollama / Redis. 32 entities, 28 collections, 29.43 MB, 12 pages,
  8 adapters — all exact.

## Upheld — fixes applied, and one set deliberately left alone

| Finding | Fix |
|---|---|
| `fzf` / `unoconv` / real `httpie` missing | Added to `Dockerfile.tools`. Root cause was worse than reported: an uncommitted change adding `unoconv` **had never been built**, so the running image was stale relative to the working tree. |
| `ulimit -n` = 1024 | New `sandbox.nofile_limit` (default 65535) → `--ulimit nofile`. Chromium and parallel Node builds were hitting `EMFILE`, which surfaces as an unrelated-looking crash. |
| No `MINIMAX_API_KEY` in sandbox | The key was set on the *gateway* all along; `config.yaml` had no `sandbox.environment` block, so nothing was forwarded. Added. |
| `python` 3.12 vs `python3` 3.10 | **Left as designed** — repointing `python3` breaks the base image's supervisord services and has broken images before (documented in `Dockerfile.tools`). Instead `python3` now appears in `/etc/nova-sandbox.json` beside `python`, so the split is readable rather than a trap. |
| Skill's Verification Ritual hardcodes `/mnt/user-data/workspace` | **Not changed** — documented only, by decision. Likely what made the doc read as hallucinated. |
| `33,665` seeded records | Real figure **33,326** — stale by 1.02%; the project's own git history had already corrected it (`README.md:129`). **Skill file left as-is**, by decision. |
| `API Routes (18)` | Header off by one; the enumerated list has 17 and matches the route files 1:1. **Not changed**, by decision. |

Note on httpie: it cannot come from apt. Jammy ships 2.6.0, which imports `DEFAULT_CIPHERS` from
`urllib3.util.ssl_` — removed in urllib3 2.x, which this image already carries. The distro package
ImportErrors on every invocation. **The build gate caught this** and the broken version never
shipped; httpie now installs into 3.12 (3.2.4).

## Two corrections to our own positioning

**Licensing.** The repo is *not* "closed source, Ali Technologies". Root `LICENSE` is the
unmodified MIT naming ByteDance and the DeerFlow Authors, preserved as MIT §3 requires. `NOTICE.md`
asserts Ali Technologies' copyright over the derivative work and carves out proprietary additions;
`OPEN_CORE.md` defines the boundary. This is **open core**, and it is the legally sound
arrangement — relicensing MIT-derived upstream wholesale would breach the licence it arrived under.
Describing it as fully proprietary claims a position the project does not hold and does not need.

**"Nova SDK".** The refactor is real: 310 commits since `fork-v3-baseline`, **1,037 files,
127,356 insertions, 14,843 deletions** over two months. (The raw diff says 2,006,548 insertions,
but that is inflated by a generated 900k-line `graphify-out/graph.json` counted twice — cite the
smaller number; it is still substantial.) However there is no `nova` Python package: the namespace
is `deerflow` in 683 files vs 181 mentioning nova. "Nova SDK" is a product name over a `deerflow.*`
namespace — which is exactly what `OPEN_CORE.md` already documents.

## The recurring failure

`Dockerfile.tools` already notes that "an agent asking *which image am I and what is in it* had to
reconstruct the answer from `/var/log/apt/history` — and got it wrong twice." The remedy exists:
`/etc/nova-sandbox.json` and `$NOVA_SANDBOX_BUILD` sit in every running sandbox. This probe found
the second and did not resolve it against git. **That is the third time.**

The fix is not a better prompt — it is making the manifest the first thing a self-probing agent
is told to read.

---

## Deliberately not changed

The three `apex-seo-platform` SKILL.md items above (`33,665`, `API Routes (18)`, the hardcoded
`/mnt/user-data/workspace`) are **recorded here but left in place**. They are documented so the
next reader knows the numbers are stale by 1.02% and the ritual path is not portable — not so
that anyone edits the file. The file is root-owned and stays as authored.

---

## Second self-probe (2026-08-25) — what was confirmed, fixed, and disproved

A later in-sandbox probe raised a fresh list. Recorded here so the next reader
does not re-investigate settled items.

### Confirmed, and fixed

- **`TINYPROXY_PORT=8118` and `MCP_SERVER_PORT=8089` advertise nothing.** Both
  are exported by the upstream base image, which starts neither daemon. `env`
  shows two services `ss -ltn` cannot find. Real, but not a Nova defect — and
  now stated outright in `/etc/nova-sandbox.json`'s `services` block and in
  `docker/sandbox/README.md`, because this had been filed as a Nova bug twice.
- **Multi-line `bash` failed about half the time.** Not on the probe's list, and
  the worst defect present: 10/10 multi-line commands failed against the live
  container versus 0/10 single-line, and one real run lost 13 of 25. Fixed by
  base64-wrapping the script so no newline reaches the upstream server.
- **Disk was 85% full.** Real. Still is; `docker builder prune` recovers ~45 GB
  and needs an explicit go-ahead on this box.

### Disproved

- **The "8-hour clock skew" is a display artifact.** Sandbox, gateway and host
  all agree in UTC to the second; the probe's own log records `date -u` →
  `Sun Aug 23 07:37:19 PM UTC 2026` beside a `CST` local rendering. The base
  image simply displays CST. `TZ: UTC` is now set in `sandbox.environment` so
  the illusion stops, but there was never a skew to correct.
- **`/app` "missing"** — the probe's own log settles it: `ls: cannot access
  '/app'`, while the sandbox's `python-server` package lives at
  `/opt/python3.12/.../site-packages/app/`. That is an upstream package named
  `app`, not a Nova path.
- **`PROMPT_COMMAND`, the bash tool's preview short-circuit, and the browser UA**
  belong to the harness running *inside* the sandbox, not to Nova. Same category
  error as the first probe: auditing the container Nova spawns and attributing
  what it finds to the system that built it.

### Not Nova's to fix

- **`MINIMAX_API_KEY` is rejected** (`error 2049: invalid api key`). The key is
  forwarded correctly — `sandbox.environment` passes it through — but the
  credential itself is invalid. No code change can fix that.
