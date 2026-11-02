"""Detectors — language-agnostic and per-language project analysis.

Phase C9 — detectors classify the workspace and extract structured
metadata without modifying any files.
"""

from __future__ import annotations

from deerflow.workspace.detectors.command_detector import CommandDetector
from deerflow.workspace.detectors.fingerprint_detector import FingerprintDetector
from deerflow.workspace.detectors.language_detector import DetectedLanguage, LanguageDetector
from deerflow.workspace.detectors.project_detector import ProjectDetector

__all__ = [
    "FingerprintDetector",
    "ProjectDetector",
    "DetectedLanguage",
    "LanguageDetector",
    "CommandDetector",
]
