#!/usr/bin/env python3
"""nova-visitors — read the ethical first-party visitor log and report who came.

The log is JSONL written by nginx (docker/nginx/nginx.conf, log_format
`visitors`) to ../logs/nova/access.jsonl. It records real Cloudflare visitor
IPs + country, path (query-string stripped), status, and user-agent — no
cookies, bodies, or tokens. See docs/VISITOR_LOGGING.md.

Usage:
  nova-visitors.py                 # summary of the last 24h
  nova-visitors.py --since 7d      # window: 30m / 12h / 7d
  nova-visitors.py --recent 40     # last N real visits (chronological)
  nova-visitors.py --security      # only the abuse/probe section
  nova-visitors.py --ip 1.2.3.4    # everything one visitor did
  nova-visitors.py --prune 30      # delete lines older than 30 days (retention)

Read-only except --prune. No third-party calls; pure log analysis.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

DEFAULT_LOG = os.environ.get(
    "NOVA_ACCESS_LOG",
    os.path.join(os.path.dirname(__file__), "..", "logs", "nova", "access.jsonl"),
)

# Paths that only a scanner/exploit-kit would request — hitting these is a
# strong signal of hostile reconnaissance, not a real user.
PROBE_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"/\.env", r"/\.git", r"/wp-admin", r"/wp-login", r"/xmlrpc\.php",
        r"/phpmyadmin", r"/\.aws", r"/\.ssh", r"/actuator", r"/vendor/",
        r"/config\.(?:json|php|yaml)", r"/\.well-known/(?!acme)", r"/cgi-bin",
        r"/administrator", r"/solr", r"/boaform", r"/hudson", r"\.php$",
    )
]

_DUR = re.compile(r"^(\d+)([mhd])$")


def parse_since(s: str) -> timedelta:
    m = _DUR.match(s.strip())
    if not m:
        raise SystemExit(f"bad --since '{s}' (use e.g. 30m, 12h, 7d)")
    n, unit = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


def load(path: str):
    if not os.path.exists(path):
        raise SystemExit(
            f"no log yet at {path}\n"
            "Traffic is captured once nginx has served real (non-probe) requests "
            "after the visitor-logging change was deployed."
        )
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def ts_of(rec: dict):
    try:
        return datetime.fromisoformat(rec["ts"])
    except (KeyError, ValueError):
        return None


def is_probe(rec: dict) -> bool:
    path = rec.get("path", "")
    return any(p.search(path) for p in PROBE_PATTERNS)


C = {
    "b": "\033[1m", "dim": "\033[2m", "cy": "\033[36m", "vi": "\033[35m",
    "rd": "\033[31m", "gn": "\033[32m", "yl": "\033[33m", "x": "\033[0m",
}
if not sys.stdout.isatty():
    C = {k: "" for k in C}


def h(title: str):
    print(f"\n{C['b']}{C['vi']}{title}{C['x']}")


def summary(recs, window_label):
    if not recs:
        print(f"No real visitor requests in the last {window_label}.")
        return
    ips = Counter(r.get("ip", "?") for r in recs)
    countries = Counter(r.get("country", "") or "??" for r in recs)
    paths = Counter(r.get("path", "?") for r in recs)
    statuses = Counter(str(r.get("status", "?")) for r in recs)
    probes = [r for r in recs if is_probe(r)]

    print(f"{C['b']}Nova visitors — last {window_label}{C['x']}")
    print(f"  {C['cy']}{len(recs):>6}{C['x']} requests")
    print(f"  {C['cy']}{len(ips):>6}{C['x']} unique visitors (IPs)")
    print(f"  {C['cy']}{len(countries):>6}{C['x']} countries")
    if probes:
        print(f"  {C['rd']}{len(probes):>6}{C['x']} probe/scanner requests {C['dim']}(see --security){C['x']}")

    h("Countries")
    for cc, n in countries.most_common(10):
        print(f"  {cc:<4} {n}")

    h("Top visitors")
    for ip, n in ips.most_common(10):
        last = max((r for r in recs if r.get("ip") == ip), key=lambda r: r.get("ts", ""))
        cc = last.get("country") or "??"
        ua = short_ua(last.get("ua", ""))
        flag = f" {C['rd']}⚠ probing{C['x']}" if any(is_probe(r) for r in recs if r.get("ip") == ip) else ""
        print(f"  {C['cy']}{ip:<40}{C['x']} {cc:<3} {n:>4} reqs  {C['dim']}{ua}{C['x']}{flag}")

    h("Top paths")
    for p, n in paths.most_common(12):
        print(f"  {n:>4}  {p}")

    h("Status codes")
    print("  " + "   ".join(f"{s}:{n}" for s, n in sorted(statuses.items())))


def short_ua(ua: str) -> str:
    if not ua or ua == "-":
        return "(no UA)"
    for name in ("Edg", "Chrome", "Firefox", "Safari", "bot", "curl", "python", "Go-http"):
        if name.lower() in ua.lower():
            m = re.search(r"(Windows NT [\d.]+|Macintosh|iPhone|iPad|Android[^;)]*|X11[^)]*)", ua)
            plat = m.group(1).strip() if m else ""
            return f"{name} · {plat}" if plat else name
    return ua[:48]


def security(recs):
    probes = [r for r in recs if is_probe(r)]
    by_ip_4xx = defaultdict(int)
    by_ip_auth = defaultdict(int)
    for r in recs:
        st = r.get("status", 0)
        if isinstance(st, int) and 400 <= st < 500:
            by_ip_4xx[r.get("ip", "?")] += 1
        if r.get("path", "").startswith("/api/v1/auth/") and st in (401, 403, 429):
            by_ip_auth[r.get("ip", "?")] += 1

    h("Security — scanner / exploit probes")
    if probes:
        seen = Counter((r.get("ip"), r.get("path")) for r in probes)
        for (ip, path), n in seen.most_common(25):
            cc = next((r.get("country") for r in probes if r.get("ip") == ip), "??")
            print(f"  {C['rd']}{ip:<40}{C['x']} {cc:<3} {n:>3}x  {path}")
    else:
        print(f"  {C['gn']}none — no requests to known exploit paths.{C['x']}")

    noisy = [(ip, n) for ip, n in by_ip_4xx.items() if n >= 10]
    h("Security — high 4xx (possible fuzzing/brute)")
    if noisy:
        for ip, n in sorted(noisy, key=lambda x: -x[1])[:15]:
            print(f"  {C['yl']}{ip:<40}{C['x']} {n} 4xx responses")
    else:
        print(f"  {C['gn']}none — no IP with 10+ 4xx.{C['x']}")

    h("Security — repeated auth failures / rate-limits")
    if by_ip_auth:
        for ip, n in sorted(by_ip_auth.items(), key=lambda x: -x[1])[:15]:
            print(f"  {C['yl']}{ip:<40}{C['x']} {n} failed/limited auth hits")
    else:
        print(f"  {C['gn']}none.{C['x']}")


def show_ip(recs, ip):
    hits = [r for r in recs if r.get("ip") == ip]
    if not hits:
        print(f"No requests from {ip} in this window.")
        return
    cc = hits[-1].get("country") or "??"
    print(f"{C['b']}{ip}{C['x']}  country={cc}  {len(hits)} requests")
    print(f"  UA: {C['dim']}{hits[-1].get('ua','')[:100]}{C['x']}")
    if any(is_probe(r) for r in hits):
        print(f"  {C['rd']}⚠ requested known exploit paths — treat as hostile.{C['x']}")
    h("Timeline")
    for r in hits[-60:]:
        st = r.get("status", "?")
        col = C["rd"] if isinstance(st, int) and st >= 400 else C["dim"]
        print(f"  {r.get('ts','')[:19]}  {col}{st}{C['x']}  {r.get('method','')} {r.get('path','')}")


def recent(recs, n):
    h(f"Last {n} real visits")
    for r in recs[-n:]:
        st = r.get("status", "?")
        col = C["rd"] if isinstance(st, int) and st >= 400 else C["gn"]
        cc = r.get("country") or "??"
        print(f"  {r.get('ts','')[:19]}  {C['cy']}{r.get('ip',''):<40}{C['x']} {cc:<3} "
              f"{col}{st}{C['x']} {r.get('method',''):<4} {r.get('path','')[:48]:<48} "
              f"{C['dim']}{short_ua(r.get('ua',''))}{C['x']}")


def prune(path: str, days: int):
    if not os.path.exists(path):
        print("nothing to prune")
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    kept, dropped = [], 0
    for rec in load(path):
        t = ts_of(rec)
        if t is None or t.astimezone(timezone.utc) >= cutoff:
            kept.append(json.dumps(rec, separators=(",", ":")))
        else:
            dropped += 1
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("\n".join(kept) + ("\n" if kept else ""))
    os.replace(tmp, path)
    print(f"pruned {dropped} lines older than {days}d; kept {len(kept)}.")


def main():
    ap = argparse.ArgumentParser(description="Nova ethical visitor log viewer.")
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--since", default="24h", help="window: 30m / 12h / 7d (default 24h)")
    ap.add_argument("--recent", type=int, metavar="N", help="show last N real visits")
    ap.add_argument("--security", action="store_true", help="only the security section")
    ap.add_argument("--ip", help="drill into one visitor")
    ap.add_argument("--prune", type=int, metavar="DAYS", help="delete lines older than DAYS (retention)")
    args = ap.parse_args()

    path = os.path.abspath(args.log)
    if args.prune is not None:
        prune(path, args.prune)
        return

    cutoff = datetime.now(timezone.utc) - parse_since(args.since)
    recs = []
    for rec in load(path):
        t = ts_of(rec)
        if t is None or t.astimezone(timezone.utc) >= cutoff:
            recs.append(rec)

    if args.ip:
        show_ip(recs, args.ip)
    elif args.recent:
        recent(recs, args.recent)
    elif args.security:
        security(recs)
    else:
        summary(recs, args.since)
        security(recs)
        print(f"\n{C['dim']}Ethics: first-party security log · 30-day retention · no query "
              f"strings/cookies/bodies stored · docs/VISITOR_LOGGING.md{C['x']}")


if __name__ == "__main__":
    main()
