#!/usr/bin/env python3
"""Generate the typed frontend client from ``contracts/capabilities.baseline.json``.

    cd backend && PYTHONPATH=. uv run python scripts/gen_capabilities_client.py

Writes ``frontend/src/core/capabilities/generated.ts``: one TypeScript
interface per operation input/output, the ``CapabilityOps`` map (name →
{input, output}) and the ``OP_META`` table (kind, module, flag, admin_only).
The output is a pure function of the contract, so CI regenerates it and
fails on a diff (``--check``). No dependency beyond the standard library:
the schema subset pydantic emits for these models is small and pinned by
the contract test.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "capabilities.baseline.json"
OUT = ROOT / "frontend" / "src" / "core" / "capabilities" / "generated.ts"


def _pascal(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"[._\-]", name) if part)


def _ts_type(schema: dict[str, Any], defs: dict[str, Any]) -> str:
    if "$ref" in schema:
        return _pascal(schema["$ref"].rsplit("/", 1)[-1])
    if "anyOf" in schema:
        return " | ".join(sorted({_ts_type(s, defs) for s in schema["anyOf"]}))
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])
    if "const" in schema:
        return json.dumps(schema["const"])
    t = schema.get("type")
    if t == "string":
        return "string"
    if t in ("integer", "number"):
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "null":
        return "null"
    if t == "array":
        return f"Array<{_ts_type(schema.get('items', {}), defs)}>"
    if t == "object":
        props = schema.get("properties")
        if props:
            return _inline_object(schema, defs)
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            return f"Record<string, {_ts_type(extra, defs)}>"
        return "Record<string, unknown>"
    return "unknown"


def _inline_object(schema: dict[str, Any], defs: dict[str, Any]) -> str:
    required = set(schema.get("required", []))
    lines = []
    for key, sub in sorted(schema.get("properties", {}).items()):
        opt = "" if key in required else "?"
        lines.append(f"{json.dumps(key)}{opt}: {_ts_type(sub, defs)}")
    return "{ " + "; ".join(lines) + " }"


def _interface(name: str, schema: dict[str, Any], defs: dict[str, Any]) -> str:
    required = set(schema.get("required", []))
    body = []
    for key, sub in sorted(schema.get("properties", {}).items()):
        desc = sub.get("description")
        if desc:
            body.append(f"  /** {desc.replace('*/', '* /')} */")
        opt = "" if key in required else "?"
        body.append(f"  {json.dumps(key)}{opt}: {_ts_type(sub, defs)};")
    inner = "\n".join(body) if body else "  // no fields"
    return f"export interface {name} {{\n{inner}\n}}"


def generate(contract: dict[str, Any]) -> str:
    out: list[str] = [
        "// GENERATED FILE — do not edit.",
        "// Source: contracts/capabilities.baseline.json via backend/scripts/gen_capabilities_client.py",
        "// Regenerate: cd backend && PYTHONPATH=. uv run python scripts/gen_capabilities_client.py",
        "",
    ]
    emitted: dict[str, str] = {}

    def emit_defs(schema: dict[str, Any]) -> None:
        for dname, dschema in sorted(schema.get("$defs", {}).items()):
            tname = _pascal(dname)
            text = _interface(tname, dschema, {})
            if tname in emitted and emitted[tname] != text:
                raise SystemExit(f"conflicting $defs for {tname}")
            emitted[tname] = text

    ops = contract["operations"]
    for op in ops:
        emit_defs(op["input_schema"])
        emit_defs(op["output_schema"])
    for op in ops:
        base = _pascal(op["name"])
        for suffix, key in (("Input", "input_schema"), ("Output", "output_schema")):
            schema = dict(op[key])
            tname = f"{base}{suffix}"
            emitted[tname] = _interface(tname, schema, schema.get("$defs", {}))

    out.extend(emitted[name] + "\n" for name in emitted)
    out.append("export interface CapabilityOps {")
    for op in ops:
        base = _pascal(op["name"])
        out.append(f"  {json.dumps(op['name'])}: {{ input: {base}Input; output: {base}Output }};")
    out.append("}\n")
    out.append("export type CapabilityOpName = keyof CapabilityOps;\n")
    out.append('export type OpKind = "read" | "write" | "execute" | "secret" | "admin";\n')
    out.append("export interface OpMeta {\n  module: string;\n  kind: OpKind;\n  description: string;\n  flag: string | null;\n  admin_only: boolean;\n  harness: boolean;\n  mcp: boolean;\n}\n")
    out.append("export const OP_META: Record<CapabilityOpName, OpMeta> = {")
    for op in ops:
        meta = {k: op[k] for k in ("module", "kind", "description", "flag", "admin_only", "harness", "mcp")}
        out.append(f"  {json.dumps(op['name'])}: {json.dumps(meta, ensure_ascii=False)},")
    out.append("};\n")
    out.append("export const CAPABILITY_MODULES = " + json.dumps([{k: m[k] for k in ("id", "title", "description", "flag", "config_key", "operations")} for m in contract["modules"]], ensure_ascii=False, indent=2) + " as const;\n")
    out.append(f"export const CONTRACT_VERSION = {contract['version']};")
    return "\n".join(out) + "\n"


def _prettier(text: str) -> str:
    """Format with the frontend's pinned prettier so `pnpm format` agrees.
    Falls back to the raw text when node_modules is absent (the drift check
    in CI always has it installed)."""
    import shutil
    import subprocess

    frontend = ROOT / "frontend"
    binary = frontend / "node_modules" / ".bin" / "prettier"
    if not binary.exists() or shutil.which("node") is None:
        return text
    proc = subprocess.run([str(binary), "--stdin-filepath", str(OUT)], input=text, capture_output=True, text=True, cwd=frontend, check=False)
    return proc.stdout if proc.returncode == 0 and proc.stdout else text


def main(argv: list[str]) -> int:
    text = _prettier(generate(json.loads(CONTRACT.read_text(encoding="utf-8"))))
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            sys.stderr.write(f"{OUT.relative_to(ROOT)} is out of date; run scripts/gen_capabilities_client.py\n")
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
