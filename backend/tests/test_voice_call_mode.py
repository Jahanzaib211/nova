"""Voice as a call, not a dictation box (2026-10-31).

Two things made a spoken turn feel broken even with working engines:

* It ran the deployment default model with extended thinking on, which spends
  the silence after the caller stops talking before a single word comes back.
  ``speech.agent`` picks the voice model and thinking defaults to off.
* Kokoro read the model's screen formatting aloud -- asterisks, code fences,
  URLs character by character. ``speakable`` turns it into words.
"""

from __future__ import annotations

import pytest

from app.gateway.routers.voice import speakable


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("**Important:** see [the docs](https://x.y/z) now.", "Important: see the docs now."),
        ("Visit https://nova.example/a?b=1 today.", "Visit the link today."),
        ("```python\nprint(1)\n``` Done.", "Done."),
        ("## Steps\n- first item\n- second step", "Steps. first item. second step"),
        ("snake_case_name stays", "snake_case_name stays"),
        ("2 * 3 = 6", "2 * 3 = 6"),
        ("_really_ **very** good", "really very good"),
        ("Plain sentence.", "Plain sentence."),
    ],
)
def test_speakable(raw, spoken):
    assert speakable(raw) == spoken


def test_voice_turns_default_to_no_thinking_and_the_deployment_model(monkeypatch):
    from deerflow.speech import registry

    monkeypatch.setattr(registry, "_merged_config", lambda: {"enabled": True})
    assert registry.voice_agent_settings() == (None, False)


def test_speech_agent_selects_the_voice_model(monkeypatch):
    from deerflow.speech import registry

    monkeypatch.setattr(registry, "_merged_config", lambda: {"agent": {"model": "minimax-m3.1-flash", "thinking": True}})
    assert registry.voice_agent_settings() == ("minimax-m3.1-flash", True)


def test_malformed_agent_section_falls_back(monkeypatch):
    from deerflow.speech import registry

    monkeypatch.setattr(registry, "_merged_config", lambda: {"agent": "fast"})
    assert registry.voice_agent_settings() == (None, False)
