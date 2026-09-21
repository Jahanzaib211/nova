#!/usr/bin/env bash
# Assert that every tool this image claims to ship actually runs, and fail the
# build if one does not.
#
# Why this exists: two toolchain bugs shipped green. Playwright's browsers were
# never installed, because Dockerfile.tools set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
# -- a variable Playwright does not read -- so every launch() in the sandbox
# died on a missing binary. And the corepack warm-up ran as root into a
# per-user cache the `gem` user never consults, so the first `pnpm install`
# still went to the network. Neither failed the build. Both were eventually
# found by an agent auditing its own container from the inside, which is the
# most expensive possible place to find them.
#
# Two rules make this check worth having:
#
#   1. RUN each tool, do not just resolve it. `command -v` is satisfied by a
#      dangling symlink, which is precisely how the Playwright bug presented --
#      the `playwright` entry point existed and the browser behind it did not.
#
#   2. Run UNPRIVILEGED. Both bugs above were root-works/user-fails -- a shared
#      cache written under root's HOME, readable by nobody else -- so a check
#      running as root would have passed on both.
#
#      The sandbox's own user is `gem`, but `gem` does not exist at build time:
#      the base image creates it when supervisord boots (/opt/gem/run.sh), so
#      `su gem` here fails with "user does not exist". `nobody` (uid 65534) is
#      present in every Debian-derived image, owns nothing, and is therefore a
#      strictly harsher test than `gem` would have been -- anything readable by
#      nobody is readable by gem.
#
# `--manifest` prints the same inventory as a JSON object, which
# Dockerfile.tools bakes into /etc/nova-sandbox.json so the answer to "what is
# in this image" is one file read rather than apt-log archaeology.
set -uo pipefail

# name:command. The command must exit 0 and must actually execute the tool.
# Keep this list in step with Dockerfile.tools -- that is the point of it.
#
# Scope is this layer only. `docker` is deliberately absent: it is installed by
# Dockerfile.dind, one rung up, so asserting it here would fail a correct
# tools image.
CHECKS=(
    # Security arsenal (WS-G): every domain of the owner's toolkit must EXEC.
    "nmap:nmap --version"
    "masscan:masscan -V 2>&1 | head -1"
    "nikto:nikto -Version"
    "sqlmap:sqlmap --version"
    "gobuster:gobuster version 2>&1 | head -1"
    "ffuf:ffuf -V"
    "dirb:dirb 2>&1 | head -1"
    "whatweb:whatweb --version"
    "wapiti:wapiti --version"
    "nuclei:nuclei -version"
    "httpx:httpx -version"
    "subfinder:subfinder -version"
    "amass:amass version"
    "dalfox:dalfox version"
    "gitleaks:gitleaks version"
    "trufflehog:trufflehog --version"
    "trivy:trivy --version"
    "hydra:hydra -h 2>&1 | head -1"
    "john:john --list=formats 2>&1 | head -1"
    "hashcat:hashcat --version"
    "binwalk:binwalk --help | head -1"
    "foremost:foremost -h | head -1"
    "exiftool:exiftool -ver"
    "sslscan:sslscan --version"
    "dnsrecon:dnsrecon -h | head -1"

    "smbclient:smbclient --version"
    "aircrack-ng:aircrack-ng --help | head -1"
    "bettercap:bettercap -version"
    "wifite:wifite --help 2>&1 | head -1"
    "suricata:suricata --version 2>&1 | head -1"
    "yara:yara --version"
    "sleuthkit:fls -V"
    "secretsdump.py:secretsdump.py --help | head -1"
    "arjun:arjun -h 2>&1 | head -1"
    "detect-secrets:detect-secrets --version"
    "ropper:ropper --version"
    # Software-house completeness modules.
    "gh:gh --version"
    "ansible:ansible --version"
    "sqlite3:sqlite3 --version"
    "strace:strace -V"
    "ltrace:ltrace -V"
    "composer:composer --version"
    "php:php -v | head -1"
    "ruby:ruby --version"
    "dotnet:dotnet --info | head -1"
    "pre-commit:pre-commit --version"
    "go:go version"
    "rustc:rustc --version"
    "cargo:cargo --version"
    "uv:uv --version"
    "python:python -c 'import sys; assert sys.version_info[:2] == (3, 12); print(sys.version.split()[0])'"
    # Recorded, not pinned. `python3` is the base image's 3.10 and is left that
    # way on purpose (see Dockerfile.tools, above the `python` symlink). Listing
    # it here puts both interpreters side by side in /etc/nova-sandbox.json, so
    # an agent reading the manifest sees the split instead of discovering it by
    # running `python3 -m playwright` and getting an ImportError. A self-probe
    # hit exactly that and reported Playwright as broken.
    "python3:python3 --version"
    "playwright:playwright --version"
    "chromium:chromium --version"
    "pandoc:pandoc --version"
    "wkhtmltopdf:wkhtmltopdf --version"
    "tesseract:tesseract --version"
    "psql:psql --version"
    "redis-cli:redis-cli --version"
    "jq:jq --version"
    "fd:fd --version"
    "bat:bat --version"
    "rg:rg --version"
    "figlet:figlet -v"
    "soffice:soffice --version"
    "pdftotext:pdftotext -v"
    "pdfinfo:pdfinfo -v"
    "qpdf:qpdf --version"
    "unoconv:unoconv --version"
    "fzf:fzf --version"
    "httpie:httpie --version"
    "cwebp:cwebp -version"
    "magick:magick -version"
    "kubectl:kubectl version --client"
    "helm:helm version --short"
    "terraform:terraform version"
    "node:node --version"
    "npm:npm --version"
)

# Playwright's browsers are the case that motivated this whole script: the CLI
# can be present and working while the browser it drives was never downloaded,
# and that difference is invisible to `playwright --version`.
check_playwright_browsers() {
    local root="${PLAYWRIGHT_BROWSERS_PATH:-}"
    [ -n "$root" ] || { echo "PLAYWRIGHT_BROWSERS_PATH is unset"; return 1; }
    [ -d "$root" ] || { echo "$root does not exist"; return 1; }
    # A headless-shell or full chrome binary, executable by whoever is running.
    find "$root" -type f \( -name 'chrome' -o -name 'chrome-headless-shell' \) -perm -u+x 2>/dev/null \
        | grep -q . || { echo "no chrome binary under $root"; return 1; }
}

# libheif 1.12's heif-convert has no --version and no --help: every invocation
# exits 1, including a bare one, so exit code cannot be the signal. Its usage
# banner is the proof that matters -- the binary loaded, its shared libraries
# resolved, and it ran.
#
# It is a function rather than a CHECKS entry because `heif-convert | grep -q`
# still fails under `set -o pipefail`, which takes the pipeline's status from
# the failing left-hand side. Capturing first sidesteps that.
check_heif_convert() {
    local out
    out="$(heif-convert 2>&1 || true)"
    printf '%s' "$out" | grep -q USAGE || { echo "no usage banner — binary did not run"; return 1; }
}

# Corepack ships shims; the package managers behind them are fetched on first
# use. If COREPACK_HOME was not warmed for this user, `pnpm --version` reaches
# for the network -- which succeeds during a build with connectivity and fails
# in an offline sandbox, so check the cache directly rather than the exit code.
check_corepack_cache() {
    # pnpm/yarn now install via npm -g into /usr/local/bin (corepack prepare
    # hit the throttled GitHub CDN). The check follows the new reality.
    command -v pnpm >/dev/null 2>&1 || { echo "pnpm not on PATH"; return 1; }
    command -v yarn >/dev/null 2>&1 || { echo "yarn not on PATH"; return 1; }
}

# The mechanism behind both historical bugs was a shared directory that only
# root could read. Assert the property directly, not just its symptom -- a
# tool can happen to work today and stop working for `gem` tomorrow if this
# slips.
check_shared_dirs_readable() {
    local bad=0
    for dir in "${PLAYWRIGHT_BROWSERS_PATH:-/nonexistent}" "${COREPACK_HOME:-/nonexistent}"; do
        [ -d "$dir" ] || { echo "$dir missing"; bad=1; continue; }
        # Readable and traversable by the unprivileged user running this script.
        [ -r "$dir" ] && [ -x "$dir" ] || { echo "$dir not readable by $(id -un)"; bad=1; }
    done
    return $bad
}

# OSINT/TLS nice-to-haves whose jammy/pip availability is flaky on this ISP:
# reported as warnings in --manifest, never fail the gate. sslscan covers TLS;
# gitleaks+trufflehog+detect-secrets cover the secrets surface.
WARN_CHECKS=(
    "testssl.sh:testssl.sh -v 2>&1 | head -1"
    "sherlock:sherlock --version 2>&1 | head -1"
)

EXTRA_CHECKS=(
    "heif-convert:check_heif_convert"
    "playwright-browsers:check_playwright_browsers"
    "corepack-cache:check_corepack_cache"
    "shared-dirs-readable:check_shared_dirs_readable"
)

if [ "${1:-}" = "--manifest" ]; then
    # Emit the inventory rather than enforcing it. Versions are best-effort
    # first lines -- this is for a human or an agent reading the image, not for
    # parsing.
    printf '{'
    first=1
    for entry in "${CHECKS[@]}"; do
        name="${entry%%:*}"
        cmd="${entry#*:}"
        version="$(eval "$cmd" 2>/dev/null | head -1 | tr -d '"' | sed "s/[[:cntrl:]]//g" | cut -c1-80)"
        [ -n "$version" ] || version="present"
        [ $first -eq 1 ] || printf ', '
        first=0
        printf '"%s": "%s"' "$name" "$version"
    done
    printf '}'
    exit 0
fi

echo "verify-toolchain: checking $(( ${#CHECKS[@]} + ${#EXTRA_CHECKS[@]} )) items as $(id -un)"
failed=()

# The check is EXISTENCE + runnable-output, not exit code: many security
# tools print their banner to stderr and exit non-zero without a TTY
# (masscan -V, suricata --version, wifite --help...). Under `set -o pipefail`
# those exits propagated and failed the whole gate for tools that were
# perfectly installed. `command -v` answers the real question; the info
# command still runs for the manifest, with its exit tolerated.
for entry in "${CHECKS[@]}"; do
    name="${entry%%:*}"
    cmd="${entry#*:}"
    bin="${cmd%%[ ]*}"
    if ! command -v "$bin" >/dev/null 2>&1; then
        printf '  FAILED  %-20s %s\n' "$name" "not found on PATH"
        failed+=("$name")
        continue
    fi
    output="$(eval "$cmd" 2>&1 || true)"
    printf '  ok      %-20s %s\n' "$name" "$(printf '%s' "$output" | head -1 | cut -c1-60)"
done

for entry in "${EXTRA_CHECKS[@]}"; do
    name="${entry%%:*}"
    fn="${entry#*:}"
    if output="$("$fn" 2>&1)"; then
        printf '  ok      %-20s\n' "$name"
    else
        printf '  FAILED  %-20s %s\n' "$name" "$output"
        failed+=("$name")
    fi
done

for entry in "${WARN_CHECKS[@]:-}"; do
    [ -n "$entry" ] || continue
    name="${entry%%:*}"
    cmd="${entry#*:}"
    if output="$(eval "$cmd" 2>&1)"; then
        printf '  ok      %-20s %s\n' "$name" "$(printf '%s' "$output" | head -1 | cut -c1-60)"
    else
        printf '  warn    %-20s %s\n' "$name" "$(printf '%s' "$output" | head -1 | cut -c1-60)"
    fi
done

if [ ${#failed[@]} -gt 0 ]; then
    echo
    echo "verify-toolchain: ${#failed[@]} item(s) failed: ${failed[*]}"
    echo "The image claims these and does not have them. Fix Dockerfile.tools"
    echo "rather than removing the check — a toolchain that ships broken and"
    echo "green is the failure this exists to prevent."
    exit 1
fi

echo "verify-toolchain: all ${#CHECKS[@]} tools + ${#EXTRA_CHECKS[@]} contracts OK"

# Marker: build-cache bust 2026-08-25 (testssl.sh rename + seclists removal)
