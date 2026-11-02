"""Language Detector — detect programming languages in the workspace.

Phase C9 — determines which languages are present and their confidence.
Used to route parsing to the right parsers.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import NamedTuple

from deerflow.workspace.models.symbol import SymbolKind
from deerflow.workspace.scanners.walker import BoundedWalker, TraversalLimit

LANGUAGE_SIGNATURES: dict[str, tuple[tuple[str, ...], float]] = {
    "python": (("*.py",), 0.9),
    "javascript": (("*.js", "*.mjs", "*.cjs"), 0.85),
    "typescript": (("*.ts", "*.tsx"), 0.9),
    "rust": (("*.rs",), 0.95),
    "go": (("*.go",), 0.95),
    "java": (("*.java",), 0.9),
    "csharp": (("*.cs",), 0.9),
    "cpp": (("*.cpp", "*.cc", "*.h", "*.hpp"), 0.7),
    "ruby": (("*.rb",), 0.9),
    "php": (("*.php",), 0.9),
    "swift": (("*.swift",), 0.95),
    "kotlin": (("*.kt", "*.kts"), 0.9),
    "yaml": (("*.yaml", "*.yml"), 0.8),
    "json": (("*.json",), 0.7),
    "toml": (("*.toml",), 0.85),
    "markdown": (("*.md", "*.mdx"), 0.6),
    "css": (("*.css", "*.scss", "*.sass"), 0.7),
    "html": (("*.html", "*.htm"), 0.6),
}


class DetectedLanguage(NamedTuple):
    language: str
    confidence: float


class LanguageDetector:
    """Detect programming languages present in a workspace.

    Uses file extension sampling — fast, O(n) scan of file list.
    Returns languages sorted by confidence.
    """

    def detect(self, root: Path, max_sample: int = 2000) -> list[DetectedLanguage]:
        """Detect languages by sampling file extensions.

        Walks up to ``max_sample`` files and counts extensions.
        Returns languages with confidence scores.
        """
        ext_counts: dict[str, int] = {}
        total = 0
        walker = BoundedWalker(root, TraversalLimit(max_depth=10, max_files=max_sample))

        for entry in walker.walk():
            for fname in entry.files:
                total += 1
                ext = Path(fname).suffix.lower()
                if ext:
                    lang = self._ext_to_lang(ext)
                    if lang:
                        ext_counts[lang] = ext_counts.get(lang, 0) + 1

        if not total:
            return []

        results: list[DetectedLanguage] = []
        for lang, count in ext_counts.items():
            base_conf = dict(LANGUAGE_SIGNATURES).get(lang, ("*", 0.5))[1]
            confidence = (count / total) * base_conf
            confidence = min(confidence, 1.0)
            results.append(DetectedLanguage(language=lang, confidence=round(confidence, 3)))

        results.sort(key=lambda x: x.confidence, reverse=True)
        return results

    def _ext_to_lang(self, ext: str) -> str | None:
        table = {
            ".py": "python",
            ".js": "javascript",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".rs": "rust",
            ".go": "go",
            ".java": "java",
            ".cs": "csharp",
            ".cpp": "cpp",
            ".cc": "cpp",
            ".cxx": "cpp",
            ".h": "cpp",
            ".hpp": "cpp",
            ".rb": "ruby",
            ".php": "php",
            ".swift": "swift",
            ".kt": "kotlin",
            ".kts": "kotlin",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".json": "json",
            ".toml": "toml",
            ".md": "markdown",
            ".mdx": "markdown",
            ".css": "css",
            ".scss": "css",
            ".sass": "css",
            ".html": "html",
            ".htm": "html",
        }
        return table.get(ext)
