"""Templates render in a Jinja2 sandbox with an allowlisted context; links are
rewritten for click tracking, the pixel is injected, and a text alternative
is derived. Sandbox escapes and unknown variables fail loudly."""

from __future__ import annotations

import pytest

from deerflow.email_marketing.templates import (
    TemplateError,
    html_to_text,
    render_template,
    rewrite_links,
    validate_template,
)
from deerflow.email_marketing.tracking import TrackingLinks

LINKS = TrackingLinks(base_url="https://nova.example.com", secret="k", send_id="s1")
CONTACT = {"email": "ada@example.com", "first_name": "Ada", "last_name": "Lovelace", "attributes": {"tier": "gold"}}


def test_renders_subject_html_text_with_merge_fields():
    out = render_template(
        subject="Hi {{ contact.first_name }}",
        html='<p>Hello {{ contact.first_name }} ({{ contact.attributes.tier }})</p><a href="https://shop.example/x">Shop</a>{{ tracking_pixel }}',
        text=None,
        contact=CONTACT,
        links=LINKS,
    )
    assert out.subject == "Hi Ada"
    assert "Hello Ada (gold)" in out.html
    assert 'href="https://nova.example.com/api/em/t/c/' in out.html
    assert "/api/em/t/o/" in out.html and ".gif" in out.html
    assert "Hello Ada (gold)" in out.text
    assert "unsubscribe" in out.html.lower()  # the footer is always present


def test_unsubscribe_url_and_view_in_browser_are_available():
    out = render_template(subject="s", html='<a href="{{ unsubscribe_url }}">bye</a>', text=None, contact=CONTACT, links=LINKS)
    assert "/api/em/u/" in out.html


def test_autoescapes_contact_values():
    out = render_template(subject="s", html="<p>{{ contact.first_name }}</p>", text=None, contact={**CONTACT, "first_name": "<script>x</script>"}, links=LINKS)
    assert "<script>" not in out.html
    assert "&lt;script&gt;" in out.html


@pytest.mark.parametrize(
    "html",
    [
        "{{ contact.__class__ }}",
        "{{ ''.__class__.__mro__ }}",
        "{% include 'x' %}",
        "{{ self._TemplateReference__context }}",
        "{{ config }}",
        "{{ cycler.__init__.__globals__ }}",
    ],
)
def test_sandbox_blocks_escapes(html):
    with pytest.raises(TemplateError):
        render_template(subject="s", html=html, text=None, contact=CONTACT, links=LINKS)


def test_unknown_variable_is_an_error_not_blank():
    with pytest.raises(TemplateError):
        render_template(subject="s", html="{{ contact.phone }}", text=None, contact=CONTACT, links=LINKS)
    with pytest.raises(TemplateError):
        render_template(subject="{{ nope }}", html="x", text=None, contact=CONTACT, links=LINKS)


def test_validate_reports_syntax_errors_without_rendering():
    assert validate_template("{{ contact.first_name }") is not None
    assert validate_template("{{ contact.first_name }}") is None


def test_rewrite_links_skips_mailto_anchors_and_unsubscribe():
    html = '<a href="mailto:a@b.c">m</a><a href="#top">t</a><a href="https://x.y/">x</a><a href="https://nova.example.com/api/em/u/tok">u</a>'
    out = rewrite_links(html, LINKS)
    assert 'href="mailto:a@b.c"' in out and 'href="#top"' in out
    assert out.count("/api/em/t/c/") == 1
    assert 'href="https://nova.example.com/api/em/u/tok"' in out


def test_html_to_text_keeps_links_and_structure():
    text = html_to_text("<h1>Title</h1><p>Para <a href='https://x.y/'>link</a></p><ul><li>a</li><li>b</li></ul>")
    assert "Title" in text and "Para link (https://x.y/)" in text
    assert "- a" in text and "- b" in text
