"""Symbol models — function, class, variable, type, etc.

Phase C9 — the symbol index replaces filesystem-based grep.
Every meaningful name in the codebase is indexed and queryable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SymbolKind(str, Enum):
    """Kind of symbol."""

    FUNCTION = "function"
    ASYNC_FUNCTION = "async_function"
    CLASS = "class"
    METHOD = "method"
    PROPERTY = "property"
    VARIABLE = "variable"
    CONSTANT = "constant"
    TYPE = "type"
    INTERFACE = "interface"
    ENUM = "enum"
    ENUM_MEMBER = "enum_member"
    PARAMETER = "parameter"
    LOCAL_VARIABLE = "local_variable"
    MODULE = "module"
    IMPORT = "import"
    EXPORT = "export"
    DECORATOR = "decorator"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SymbolLocation:
    """A single location where a symbol is defined or referenced."""

    file_path: str
    line: int
    column: int = 0
    is_definition: bool = False
    is_reference: bool = False


@dataclass(frozen=True)
class Symbol:
    """A named symbol in the workspace.

    Symbols are indexed by the symbol index and queried by name.
    """

    symbol_id: str
    project_id: str
    name: str
    kind: SymbolKind
    fqn: str
    file_path: str
    language: str
    signature: str = ""
    locations: tuple[SymbolLocation, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", SymbolKind(self.kind))
