"""Tests for the malformed-HTML risk scanner in the deterministic review.

Regression for the chunked-write clobber: an agent writing an HTML page in
parts where each write_file overwrites the previous part ships a file that
starts mid-script. The review must flag it as high risk (observed live:
maree.html, 3 writes, final file began at a JS function body while the run
claimed "verified clean").
"""

from deerflow.sandbox.review import FileChange, _scan_malformed_html_risks


def _fc(path: str) -> FileChange:
    return FileChange(path=path, status="added", added=1, removed=0)


def test_html_starting_mid_script_is_flagged(tmp_path) -> None:
    (tmp_path / "maree.html").write_text(" tideToSeaLevel(h){\n  return h;\n}\n<div>tail</div>")
    risks = _scan_malformed_html_risks(tmp_path, [_fc("maree.html")])
    assert len(risks) == 1
    assert risks[0].level == "high"
    assert "maree.html" in risks[0].message


def test_proper_html_documents_pass(tmp_path) -> None:
    (tmp_path / "ok1.html").write_text("<!DOCTYPE html>\n<html><body>hi</body></html>")
    (tmp_path / "ok2.html").write_text("  \n<html lang='en'><body>hi</body></html>")
    (tmp_path / "ok3.html").write_text("<!-- banner comment -->\n<html><body>hi</body></html>")
    files = [_fc("ok1.html"), _fc("ok2.html"), _fc("ok3.html")]
    assert _scan_malformed_html_risks(tmp_path, files) == []


def test_non_html_files_are_ignored(tmp_path) -> None:
    (tmp_path / "app.js").write_text("function f(){}")
    assert _scan_malformed_html_risks(tmp_path, [_fc("app.js")]) == []


def test_deleted_and_missing_files_are_skipped(tmp_path) -> None:
    deleted = FileChange(path="gone.html", status="deleted", added=0, removed=10)
    missing = _fc("never-written.html")
    assert _scan_malformed_html_risks(tmp_path, [deleted, missing]) == []
