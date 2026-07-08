"""Tests for ArtifactDelivery service."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.channels.services.artifact_delivery import ArtifactDelivery


@pytest.fixture
def artifact_delivery():
    """Create an ArtifactDelivery service."""
    return ArtifactDelivery()


class TestArtifactDeliveryFormat:
    """Tests for artifact text formatting."""

    def test_format_single_artifact(self, artifact_delivery):
        """Should format single artifact with singular label."""
        result = artifact_delivery.format_artifact_text(["/mnt/user-data/outputs/report.pdf"])
        assert result == "Created File: 📎 report.pdf"

    def test_format_multiple_artifacts(self, artifact_delivery):
        """Should format multiple artifacts with plural label."""
        result = artifact_delivery.format_artifact_text([
            "/mnt/user-data/outputs/report.pdf",
            "/mnt/user-data/outputs/data.csv",
        ])
        assert result == "Created Files: 📎 report.pdf、data.csv"

    def test_format_empty_artifacts(self, artifact_delivery):
        """Should format empty list."""
        result = artifact_delivery.format_artifact_text([])
        assert result == "Created Files: 📎 "


class TestArtifactDeliveryResolve:
    """Tests for artifact resolution."""

    def test_resolve_attachments_rejects_non_outputs_path(self, artifact_delivery):
        """Should reject paths not under /mnt/user-data/outputs/."""
        with patch("deerflow.config.paths.get_paths") as mock_paths, \
             patch("deerflow.runtime.user_context.get_effective_user_id") as mock_user_id:
            mock_user_id.return_value = "test-user"
            mock_paths_instance = MagicMock()
            mock_paths.return_value = mock_paths_instance

            result = artifact_delivery.resolve_attachments(
                "thread-123",
                ["/mnt/user-data/uploads/secret.txt"],
            )

            assert result == []

    def test_resolve_attachments_resolves_valid_path(self, artifact_delivery, tmp_path):
        """Should resolve valid output paths."""
        # Create a test file
        output_dir = tmp_path / "outputs"
        output_dir.mkdir()
        test_file = output_dir / "report.pdf"
        test_file.write_text("test content")

        with patch("deerflow.config.paths.get_paths") as mock_paths, \
             patch("deerflow.runtime.user_context.get_effective_user_id") as mock_user_id:
            mock_user_id.return_value = "test-user"
            mock_paths_instance = MagicMock()
            mock_paths_instance.sandbox_outputs_dir.return_value = output_dir
            mock_paths_instance.resolve_virtual_path.return_value = test_file
            mock_paths.return_value = mock_paths_instance

            result = artifact_delivery.resolve_attachments(
                "thread-123",
                ["/mnt/user-data/outputs/report.pdf"],
            )

            assert len(result) == 1
            assert result[0].filename == "report.pdf"
            assert result[0].mime_type == "application/pdf"


class TestArtifactDeliveryPrepare:
    """Tests for prepare_artifact_delivery."""

    def test_prepare_empty_artifacts(self, artifact_delivery):
        """Should return original text when no artifacts."""
        text, attachments = artifact_delivery.prepare_artifact_delivery(
            "thread-123",
            "Here is the result",
            [],
        )

        assert text == "Here is the result"
        assert attachments == []

    def test_prepare_with_unresolved_artifacts(self, artifact_delivery):
        """Should append artifact text for unresolved paths."""
        with patch.object(artifact_delivery, "resolve_attachments", return_value=[]):
            text, attachments = artifact_delivery.prepare_artifact_delivery(
                "thread-123",
                "Here is the result",
                ["/mnt/user-data/outputs/report.pdf"],
            )

            assert "Created File" in text
            assert "report.pdf" in text
            assert attachments == []
