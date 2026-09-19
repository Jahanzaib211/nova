"""CSV import: header mapping, normalisation, dedupe within the file,
syntax validation, and a progress-reporting job over the repository."""

from __future__ import annotations

from deerflow.email_marketing.contacts_import import guess_mapping, parse_contacts_csv


def test_guess_mapping_handles_common_headers():
    m = guess_mapping(["E-Mail Address", "First name", "Surname", "Company"])
    assert m == {"email": "E-Mail Address", "first_name": "First name", "last_name": "Surname"}


def test_parse_normalises_dedupes_and_flags_invalid():
    csv_text = "email,first,last\nAda@Example.com,Ada,Lovelace\nada@example.com,Dup,Row\nnot-an-email,X,Y\n,Empty,Row\nbob@example.com,Bob,\n"
    rows, report = parse_contacts_csv(csv_text, {"email": "email", "first_name": "first", "last_name": "last"})
    assert [r["email"] for r in rows] == ["Ada@Example.com", "bob@example.com"]
    assert rows[0]["first_name"] == "Ada" and rows[1]["last_name"] is None
    assert report == {"total": 5, "valid": 2, "duplicates": 1, "invalid": 2}


def test_extra_columns_become_attributes():
    rows, _ = parse_contacts_csv("email,tier,city\na@b.co,gold,Lahore\n", {"email": "email"})
    assert rows[0]["attributes"] == {"tier": "gold", "city": "Lahore"}
