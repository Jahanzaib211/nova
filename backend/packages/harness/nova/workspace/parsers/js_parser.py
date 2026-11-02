"""JavaScript/TypeScript parser using regex-based extraction.

Phase C9 — since no AST library is available as a hard dependency,
JS/TS files are parsed using regex patterns.  This is a fallback
for files too large to load into memory.  Ideally use esprima or
ts-morph when available.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from deerflow.workspace.models.symbol import Symbol, SymbolKind, SymbolLocation

IMPORT_RE = re.compile(
    r"(?:import\s+(?:(?:\{[^}]+\}|[^;'\"{}]+)\s+from\s+)?['\"]([^'\"]+)['\"]|"
    r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\))",
    re.MULTILINE,
)

EXPORT_DEFAULT_RE = re.compile(r"export\s+default\s+(?:function|class|const|let|var)?\s*(\w+)", re.MULTILINE)
EXPORT_NAMED_RE = re.compile(r"export\s+(?:function|class|const|let|var|interface|type)\s+(\w+)", re.MULTILINE)
FUNCTION_RE = re.compile(r"(?:async\s+)?function\s+(\w+)\s*\(", re.MULTILINE)
CONST_RE = re.compile(r"(?:const|let|var)\s+(\w+)\s*=", re.MULTILINE)
CLASS_RE = re.compile(r"class\s+(\w+)(?:\s+extends\s+\w+)?(?:\s+implements\s+[\w,\s]+)?\s*\{", re.MULTILINE)
ASYNC_RE = re.compile(r"(async\s+)?function\s+(\w+)\s*\(", re.MULTILINE)


class JSParser:
    """Parse JavaScript/MJS/CJS files using regex extraction.

    Fallback parser when no JS AST library is available.
    Extracts imports, exports, and top-level symbols.
    """

    def parse_file(self, file_path: Path) -> list[Symbol]:
        """Parse a JS file and return its symbols."""
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return self.parse_source(source, str(file_path))

    def parse_source(self, source: str, file_path: str) -> list[Symbol]:
        """Parse source text and return symbols."""
        symbols = []
        lines = source.splitlines()

        for i, line in enumerate(lines, 1):
            if m := FUNCTION_RE.search(line):
                name = m.group(1)
                if not name.startswith("_"):
                    symbols.append(
                        Symbol(
                            symbol_id=uuid.uuid4().hex,
                            project_id="",
                            name=name,
                            kind=SymbolKind.FUNCTION,
                            fqn=name,
                            file_path=file_path,
                            language="javascript",
                            locations=(SymbolLocation(file_path=file_path, line=i, is_definition=True),),
                        )
                    )
            elif m := CLASS_RE.search(line):
                name = m.group(1)
                if not name.startswith("_"):
                    symbols.append(
                        Symbol(
                            symbol_id=uuid.uuid4().hex,
                            project_id="",
                            name=name,
                            kind=SymbolKind.CLASS,
                            fqn=name,
                            file_path=file_path,
                            language="javascript",
                            locations=(SymbolLocation(file_path=file_path, line=i, is_definition=True),),
                        )
                    )
            elif m := CONST_RE.match(line):
                name = m.group(1)
                if not name.startswith("_") and name[0].islower():
                    symbols.append(
                        Symbol(
                            symbol_id=uuid.uuid4().hex,
                            project_id="",
                            name=name,
                            kind=SymbolKind.VARIABLE,
                            fqn=name,
                            file_path=file_path,
                            language="javascript",
                            locations=(SymbolLocation(file_path=file_path, line=i, is_definition=True),),
                        )
                    )

        return symbols

    def extract_imports(self, source: str) -> list[str]:
        """Extract all imported module names."""
        imports = []
        for m in IMPORT_RE.finditer(source):
            name = m.group(1) or m.group(2)
            if name:
                imports.append(name.split("/")[0].split(".")[0])
        return list(set(imports))

    def extract_exports(self, source: str) -> list[str]:
        """Extract exported names."""
        exports = []
        for m in EXPORT_NAMED_RE.finditer(source):
            exports.append(m.group(1))
        for m in EXPORT_DEFAULT_RE.finditer(source):
            exports.append(m.group(1))
        return list(set(exports))
