---
name: security
description: >
  Complete security arsenal map for the Nova sandbox — every installed tool
  by domain, dependency-ordered workflows, and the methodology library mounted
  at /mnt/security-toolkit. Use before any recon, scan, exploit, hardening or
  audit task.
license: Proprietary
allowed-tools:
  - bash
  - read_file
  - write_file
  - str_replace
  - grep_files
  - browser_navigate
---

# Security Arsenal — Domain Map

The sandbox ships a full security toolchain AND this methodology library.
**Read `/mnt/security-toolkit/<domain>.md` BEFORE using a domain's tools** —
each cheat covers flags, safe defaults and watch-outs you will otherwise get
wrong.

## Dependency-ordered workflows (software-house discipline)

1. **Recon** → subfinder → amass → httpx → (osint.md, sherlock, theHarvester)
2. **Network map** → nmap (-sV --top-ports 1000) → masscan (large ranges) → tshark (capture)
3. **Web app** → whatweb → wafw00f? → ffuf/gobuster/dirb (content) → nikto/wapiti (scan) → sqlmap/dalfox/arjun (exploit)
4. **Secrets** → gitleaks (git history!) → trufflehog (fs+git) → detect-secrets/semgrep (baseline in CI)
5. **Exploit** → searchsploit-style thinking with nuclei templates first; msf NOT installed (deliberate)
6. **Creds/AD** → hydra/john/hashcat (+ rules from seclists/Passwords), enum4linux/smbclient/impacket-secretsdump
7. **Forensics/reverse** → sleuthkit (fls/icat), binwalk, foremost, exiftool, yara
8. **Blue team/hardening** → suricata (ids), lynis, chkrootkit; trivy (images/IaC)

## Tool inventory truth

Run `cat /etc/nova-sandbox.json | jq .toolchain,.security` — the image states
what it has. Never assume a binary exists; verify with `command -v`.

## Methodology library

/mnt/security-toolkit/: network.md vulnscan.md webapp.md secrets.md
exploit.md osint.md forensics.md wireless.md blueteam.md ad.md cloud.md
reverse.md + CHEATSHEET.md (quick reference).

## Watch-outs (from the cheatsheets — do not skip)

- Masscan on shared networks = incident report. Scope targets explicitly.
- sqlmap needs `--batch` in automation and a target you OWN.
- Nuclei first run downloads templates (~200MB): do it once, keep cache warm.
- Wireless tools need monitor-mode hardware the container does not have —
  analysis of captured files only.
