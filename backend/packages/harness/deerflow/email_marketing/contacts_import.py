"""CSV contact import: header mapping, normalisation, dedupe, validation."""

from __future__ import annotations

import csv
import io
import re
from typing import Any

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_ALIASES = {
    "email": ("email", "e-mail", "e-mail address", "email address", "mail", "emailaddress"),
    "first_name": ("first_name", "first name", "firstname", "first", "given name", "given_name", "name"),
    "last_name": ("last_name", "last name", "lastname", "last", "surname", "family name", "family_name"),
}


def guess_mapping(headers: list[str]) -> dict[str, str]:
    """Field → header, for the headers a human would map the same way."""
    out: dict[str, str] = {}
    lowered = {h.strip().lower(): h for h in headers}
    for field, names in _ALIASES.items():
        for name in names:
            if name in lowered and lowered[name] not in out.values():
                out[field] = lowered[name]
                break
    return out


def is_valid_email(value: str) -> bool:
    return bool(value) and len(value) <= 320 and _EMAIL_RE.match(value) is not None


def parse_contacts_csv(text: str, mapping: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Rows ready for ``upsert_contact`` plus a count report.

    ``mapping`` is field → CSV header (``email`` required). Every column not
    mapped becomes a contact attribute. Duplicates within the file (by
    normalised email) keep the first row; invalid or empty emails are counted
    and dropped.
    """
    email_col = mapping.get("email")
    if not email_col:
        raise ValueError("mapping must name the email column")
    reader = csv.DictReader(io.StringIO(text))
    mapped = {v for v in mapping.values() if v}
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    report = {"total": 0, "valid": 0, "duplicates": 0, "invalid": 0}
    for raw in reader:
        report["total"] += 1
        email = (raw.get(email_col) or "").strip()
        if not is_valid_email(email):
            report["invalid"] += 1
            continue
        key = email.lower()
        if key in seen:
            report["duplicates"] += 1
            continue
        seen.add(key)
        row: dict[str, Any] = {"email": email}
        for field in ("first_name", "last_name"):
            col = mapping.get(field)
            value = (raw.get(col) or "").strip() if col else ""
            row[field] = value or None
        row["attributes"] = {k: (v or "").strip() for k, v in raw.items() if k and k not in mapped and (v or "").strip()}
        rows.append(row)
        report["valid"] += 1
    return rows, report
