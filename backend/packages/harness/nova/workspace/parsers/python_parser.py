"""Python parser using the ``ast`` module.

Phase C9 — extracts imports, exports, and symbol definitions
from Python source files.  Falls back to regex for files that
cannot be parsed (syntax errors, encoding issues).
"""

from __future__ import annotations

import ast
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from deerflow.workspace.models.symbol import Symbol, SymbolKind, SymbolLocation


class PythonParseError(Exception):
    """Raised when a Python file cannot be parsed."""


@dataclass
class PythonParser:
    """Parse Python source files and extract symbols.

    Uses the stdlib ``ast`` module.  Handles:
    - Import statements (import X, from X import y)
    - Top-level definitions (function, class, async function)
    - __all__ exports
    - Function signatures (parameters)

    Files that cannot be parsed (SyntaxError, encoding issues) are skipped.
    """

    def parse_file(self, file_path: Path) -> list[Symbol]:
        """Parse a Python file and return its symbols."""
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        return self.parse_source(source, str(file_path))

    def parse_source(self, source: str, file_path: str) -> list[Symbol]:
        """Parse source text and return symbols."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return self._fallback_parse(source, file_path)
        return self._extract_symbols(tree, file_path, source)

    def _extract_symbols(self, tree: ast.AST, file_path: str, source: str) -> list[Symbol]:
        symbols = []
        lines = source.splitlines()
        module_name = Path(file_path).stem

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                symbols.append(
                    Symbol(
                        symbol_id=uuid.uuid4().hex,
                        project_id="",
                        name=node.name,
                        kind=SymbolKind.CLASS,
                        fqn=f"{module_name}.{node.name}",
                        file_path=file_path,
                        language="python",
                        signature=self._class_signature(node),
                        locations=(SymbolLocation(file_path=file_path, line=node.lineno or 0, is_definition=True),),
                    )
                )
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) or isinstance(item, ast.AsyncFunctionDef):
                        method_kind = SymbolKind.METHOD
                        symbols.append(
                            Symbol(
                                symbol_id=uuid.uuid4().hex,
                                project_id="",
                                name=item.name,
                                kind=method_kind,
                                fqn=f"{module_name}.{node.name}.{item.name}",
                                file_path=file_path,
                                language="python",
                                signature=self._func_signature(item),
                                locations=(SymbolLocation(file_path=file_path, line=item.lineno or 0, is_definition=True),),
                            )
                        )
            elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                kind = SymbolKind.ASYNC_FUNCTION if isinstance(node, ast.AsyncFunctionDef) else SymbolKind.FUNCTION
                symbols.append(
                    Symbol(
                        symbol_id=uuid.uuid4().hex,
                        project_id="",
                        name=node.name,
                        kind=kind,
                        fqn=f"{module_name}.{node.name}",
                        file_path=file_path,
                        language="python",
                        signature=self._func_signature(node),
                        locations=(SymbolLocation(file_path=file_path, line=node.lineno or 0, is_definition=True),),
                    )
                )

        return symbols

    def extract_imports(self, source: str) -> list[str]:
        """Extract all imported module names from source."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split(".")[0])
        return list(set(imports))

    def extract_exports(self, source: str) -> list[str]:
        """Extract names from __all__ or top-level public names."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id == "__all__":
                    return [self._eval_str_constant(node.value)]
        names = [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))]
        return [n for n in names if not n.startswith("_")]

    def _func_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = [a.arg for a in node.args.posonlyargs + node.args.args]
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")
        return f"def {node.name}({', '.join(args)})"

    def _class_signature(self, node: ast.ClassDef) -> str:
        bases = []
        for b in node.bases:
            if isinstance(b, ast.Name):
                bases.append(b.id)
            elif isinstance(b, ast.Attribute):
                bases.append(b.attr)
            elif isinstance(b, ast.Subscript):
                bases.append(self._ast_to_str(b))
            elif isinstance(b, ast.Constant) and isinstance(b.value, str):
                bases.append(b.value)
        return f"class {node.name}({', '.join(bases)})"

    def _ast_to_str(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{self._ast_to_str(node.value)}.{node.attr}"
        if isinstance(node, ast.Subscript):
            return f"{self._ast_to_str(node.value)}[{self._ast_to_str(node.slice)}]"
        if isinstance(node, ast.Constant):
            return repr(node.value)
        if isinstance(node, ast.Tuple):
            return ", ".join(self._ast_to_str(elt) for elt in node.elts)
        return ast.unparse(node) if hasattr(ast, "unparse") else ""

    def _eval_str_constant(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.List):
            return ", ".join(self._eval_str_constant(e) for e in node.elts)
        return ""

    def _fallback_parse(self, source: str, file_path: str) -> list[Symbol]:
        """Regex fallback for unparseable files."""
        symbols = []
        lines = source.splitlines()
        for i, line in enumerate(lines, 1):
            m = re.match(r"(?:def|async\s+def)\s+(\w+)", line)
            if m:
                symbols.append(
                    Symbol(
                        symbol_id=uuid.uuid4().hex,
                        project_id="",
                        name=m.group(1),
                        kind=SymbolKind.FUNCTION,
                        fqn=m.group(1),
                        file_path=file_path,
                        language="python",
                        locations=(SymbolLocation(file_path=file_path, line=i, is_definition=True),),
                    )
                )
            m = re.match(r"class\s+(\w+)", line)
            if m:
                symbols.append(
                    Symbol(
                        symbol_id=uuid.uuid4().hex,
                        project_id="",
                        name=m.group(1),
                        kind=SymbolKind.CLASS,
                        fqn=m.group(1),
                        file_path=file_path,
                        language="python",
                        locations=(SymbolLocation(file_path=file_path, line=i, is_definition=True),),
                    )
                )
        return symbols
