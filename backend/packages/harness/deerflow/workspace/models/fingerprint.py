"""Repository fingerprint — classify a repository before indexing.

Phase C9 — the first thing WIK does when entering a workspace is
classify it.  The fingerprint drives which detectors are run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RepoKind(str, Enum):
    """Repository classification."""

    EMPTY = "empty"
    DOCUMENTATION = "documentation"
    LIBRARY = "library"
    CLI = "cli"
    BACKEND = "backend"
    FRONTEND = "frontend"
    FULLSTACK = "fullstack"
    MONOREPO = "monorepo"
    POLYREPO = "polyrepo"
    HYBRID = "hybrid"
    INFRASTRUCTURE = "infrastructure"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Confidence[str]:
    """A value with an associated confidence score."""

    value: str
    confidence: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0–1.0, got {self.confidence}")


@dataclass(frozen=True)
class RepositoryFingerprint:
    """Top-level classification of a repository.

    Produced by ``FingerprintDetector`` before full indexing begins.
    Drives detector prioritization.
    """

    kind: RepoKind
    primary_language: str
    secondary_languages: tuple[str, ...] = field(default_factory=tuple)
    is_monorepo: bool = False
    is_polyrepo: bool = False
    detected_at: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", RepoKind(self.kind))
