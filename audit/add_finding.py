#!/usr/bin/env python3
"""Append one finding to audit/findings.jsonl. Usage: add_finding.py id phase sev title evidence impact fix effort blast"""
import json, sys, pathlib
keys = ["id","phase","severity","title","evidence","impact","fix","effort","blast_radius"]
assert len(sys.argv) == 10, keys
row = dict(zip(keys, sys.argv[1:]))
p = pathlib.Path(__file__).with_name("findings.jsonl")
ids = {json.loads(l)["id"] for l in p.read_text().splitlines() if l.strip()}
assert row["id"] not in ids, f"dup id {row['id']}"
with p.open("a") as f: f.write(json.dumps(row) + "\n")
print("added", row["id"])
