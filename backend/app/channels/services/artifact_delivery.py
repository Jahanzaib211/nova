"""ArtifactDelivery service for channel artifact handling.

This module provides the ArtifactDelivery service that handles artifact
resolution, file delivery, and attachment management for IM channels.
"""

from __future__ import annotations

import logging
import mimetypes
import posixpath

from app.channels.message_bus import ResolvedAttachment

logger = logging.getLogger(__name__)

_OUTPUTS_VIRTUAL_PREFIX = "/mnt/user-data/outputs/"


class ArtifactDelivery:
    """Service for resolving and delivering artifacts from agent runs.

    This service encapsulates all artifact-related operations:
    - Virtual path resolution to host paths
    - Attachment metadata generation
    - Artifact text formatting
    """

    def __init__(self) -> None:
        """Initialize the ArtifactDelivery service."""
        pass

    def resolve_attachments(
        self,
        thread_id: str,
        artifacts: list[str],
        *,
        user_id: str | None = None,
    ) -> list[ResolvedAttachment]:
        """Resolve virtual artifact paths to host filesystem paths with metadata.

        Only paths under ``/mnt/user-data/outputs/`` are accepted; any other
        virtual path is rejected with a warning to prevent exfiltrating uploads
        or workspace files via IM channels.

        Args:
            thread_id: The thread ID.
            artifacts: List of virtual artifact paths.
            user_id: Optional user ID for path resolution.

        Returns:
            List of resolved attachments.
        """
        from deerflow.config.paths import get_paths
        from deerflow.runtime.user_context import get_effective_user_id

        attachments: list[ResolvedAttachment] = []
        paths = get_paths()
        effective_user_id = user_id or get_effective_user_id()
        outputs_dir = paths.sandbox_outputs_dir(thread_id, user_id=effective_user_id).resolve()

        for virtual_path in artifacts:
            # Security: only allow files from the agent outputs directory
            if not virtual_path.startswith(_OUTPUTS_VIRTUAL_PREFIX):
                logger.warning(
                    "[ArtifactDelivery] rejected non-outputs artifact path: %s",
                    virtual_path,
                )
                continue
            try:
                actual = paths.resolve_virtual_path(thread_id, virtual_path, user_id=effective_user_id)
                # Verify the resolved path is actually under the outputs directory
                # (guards against path-traversal even after prefix check)
                try:
                    actual.resolve().relative_to(outputs_dir)
                except ValueError:
                    logger.warning(
                        "[ArtifactDelivery] artifact path escapes outputs dir: %s -> %s",
                        virtual_path,
                        actual,
                    )
                    continue
                if not actual.is_file():
                    logger.warning(
                        "[ArtifactDelivery] artifact not found on disk: %s -> %s",
                        virtual_path,
                        actual,
                    )
                    continue
                mime, _ = mimetypes.guess_type(str(actual))
                mime = mime or "application/octet-stream"
                attachments.append(
                    ResolvedAttachment(
                        virtual_path=virtual_path,
                        actual_path=actual,
                        filename=actual.name,
                        mime_type=mime,
                        size=actual.stat().st_size,
                        is_image=mime.startswith("image/"),
                    )
                )
            except (ValueError, OSError) as exc:
                logger.warning(
                    "[ArtifactDelivery] failed to resolve artifact %s: %s",
                    virtual_path,
                    exc,
                )
        return attachments

    def prepare_artifact_delivery(
        self,
        thread_id: str,
        response_text: str,
        artifacts: list[str],
        *,
        user_id: str | None = None,
    ) -> tuple[str, list[ResolvedAttachment]]:
        """Resolve attachments and append filename fallbacks to the text response.

        Args:
            thread_id: The thread ID.
            response_text: The agent's response text.
            artifacts: List of virtual artifact paths.
            user_id: Optional user ID for path resolution.

        Returns:
            Tuple of (response_text, attachments).
        """
        attachments: list[ResolvedAttachment] = []
        if not artifacts:
            return response_text, attachments

        attachments = self.resolve_attachments(thread_id, artifacts, user_id=user_id)
        resolved_virtuals = {attachment.virtual_path for attachment in attachments}
        unresolved = [path for path in artifacts if path not in resolved_virtuals]

        if unresolved:
            artifact_text = self.format_artifact_text(unresolved)
            response_text = (response_text + "\n\n" + artifact_text) if response_text else artifact_text

        # Always include resolved attachment filenames as a text fallback so files
        # remain discoverable even when the upload is skipped or fails.
        if attachments:
            resolved_text = self.format_artifact_text([attachment.virtual_path for attachment in attachments])
            response_text = (response_text + "\n\n" + resolved_text) if response_text else resolved_text

        return response_text, attachments

    def format_artifact_text(self, artifacts: list[str]) -> str:
        """Format artifact paths into a human-readable text block listing filenames.

        Args:
            artifacts: List of virtual artifact paths.

        Returns:
            Formatted text string.
        """
        filenames = [posixpath.basename(p) for p in artifacts]
        if len(filenames) == 1:
            return f"Created File: 📎 {filenames[0]}"
        return "Created Files: 📎 " + "、".join(filenames)
