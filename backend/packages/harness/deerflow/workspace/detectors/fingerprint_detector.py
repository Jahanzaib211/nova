"""Fingerprint Detector — classify a repository before full indexing.

Phase C9 — WIK inspects top-level files to classify the repository.
The fingerprint drives which detectors are run and how the plan is built.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from deerflow.workspace.models.fingerprint import Confidence, RepoKind, RepositoryFingerprint
from deerflow.workspace.scanners.ignore import should_ignore_name


@dataclass
class FingerprintDetector:
    """Classify a repository by inspecting its top-level structure.

    This is the first detector to run.  It is fast (O(1) file reads)
    and drives the prioritization of subsequent detectors.
    """

    def detect(self, root: Path) -> RepositoryFingerprint:
        """Classify the repository at ``root``."""
        top_level = self._list_top_level(root)
        kinds = self._score_kinds(top_level)
        primary = self._primary_language(top_level)
        secondary = self._secondary_languages(top_level)
        is_mono = self._is_monorepo(top_level, root)

        return RepositoryFingerprint(
            kind=kinds[0].value,
            primary_language=primary,
            secondary_languages=tuple(secondary),
            is_monorepo=is_mono,
            detected_at="",
            metadata={},
        )

    def _list_top_level(self, root: Path) -> dict[str, bool]:
        try:
            entries = {e.name: e.is_file() for e in root.iterdir()}
        except OSError:
            return {}
        return {k: v for k, v in entries.items() if not should_ignore_name(k)}

    def _score_kinds(self, top_level: dict[str, bool]) -> list[RepoKind]:
        files = {k for k, v in top_level.items() if v}
        dirs = {k for k, v in top_level.items() if not v}

        candidates: list[tuple[RepoKind, float]] = []

        if not files and not dirs:
            candidates.append((RepoKind.EMPTY, 1.0))
        elif files.intersection({"README.md", "CONTRIBUTING.md", "docs"}):
            candidates.append((RepoKind.DOCUMENTATION, 0.85))
        if files.intersection({"package.json"}):
            if "pnpm-workspace.yaml" in files or "lerna.json" in files or "nx.json" in files:
                candidates.append((RepoKind.MONOREPO, 0.95))
            elif dirs.intersection({"src", "lib"}):
                if "package.json" in files:
                    pkg = self._read_json(root / "package.json")
                    if pkg and "scripts" in pkg:
                        candidates.append((RepoKind.FRONTEND, 0.9))
                    else:
                        candidates.append((RepoKind.LIBRARY, 0.7))
            else:
                candidates.append((RepoKind.CLI, 0.6))
        if files.intersection({"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"}):
            if "pyproject.toml" in files:
                candidates.append((RepoKind.BACKEND, 0.9))
            else:
                candidates.append((RepoKind.BACKEND, 0.8))
        if files.intersection({"Cargo.toml", "Cargo.lock"}):
            candidates.append((RepoKind.CLI, 0.7))
        if files.intersection({"docker-compose.yml", "docker-compose.yaml", "Dockerfile"}):
            candidates.append((RepoKind.INFRASTRUCTURE, 0.8))
        if files.intersection({".gitlab-ci.yml", ".github", ".circleci"}):
            candidates.append((RepoKind.INFRASTRUCTURE, 0.6))
        if "Makefile" in files or "CMakeLists.txt" in files:
            candidates.append((RepoKind.CLI, 0.5))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return [k for k, _ in candidates[:3]] if candidates else [RepoKind.UNKNOWN]

    def _primary_language(self, top_level: dict[str, bool]) -> str:
        if "package.json" in top_level:
            return "javascript"
        if "pyproject.toml" in top_level or "requirements.txt" in top_level:
            return "python"
        if "Cargo.toml" in top_level:
            return "rust"
        if "go.mod" in top_level:
            return "go"
        if "pom.xml" in top_level or "build.gradle" in top_level:
            return "java"
        if "Dockerfile" in top_level or "docker-compose.yml" in top_level:
            return "yaml"
        return ""

    def _secondary_languages(self, top_level: dict[str, bool]) -> list[str]:
        secondary: list[str] = []
        if "package.json" in top_level and "pyproject.toml" in top_level:
            secondary.append("python")
        if "Cargo.toml" in top_level and "package.json" in top_level:
            secondary.append("javascript")
        return secondary

    def _is_monorepo(self, top_level: dict[str, bool], root: Path) -> bool:
        if "pnpm-workspace.yaml" in top_level:
            return True
        if "lerna.json" in top_level:
            return True
        if "nx.json" in top_level:
            return True
        if "turbo.json" in top_level:
            return True
        pkg_dirs = [k for k, v in top_level.items() if not v and k.startswith("packages")]
        if len(pkg_dirs) > 1:
            return True
        return False

    def _read_json(self, path: Path) -> dict | None:
        try:
            import json

            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
