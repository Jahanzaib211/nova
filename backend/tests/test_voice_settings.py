"""The voice settings panel's backend: overrides, and making them take effect.

Two things here are load-bearing and easy to get wrong:

1. Settings are written to a **separate overrides file**, never `config.yaml`.
   That file is operator-owned and commented, and `yaml.dump` would strip every
   comment from it.
2. Saving **must** drop the cached engines. They are process singletons, so
   without the reset the panel writes a file, reports success, and every later
   request keeps using the engine built at startup — the settings appear to work
   and do nothing at all. That is the failure this suite exists to prevent.
"""

from __future__ import annotations

import asyncio

import pytest

from deerflow.speech import registry
from deerflow.speech.engines.null import ScriptedSTT, ToneTTS


@pytest.fixture(autouse=True)
def _isolated_overrides(monkeypatch, tmp_path):
    """Never touch the developer's real ~/.deer-flow while testing."""
    path = tmp_path / "voice-settings.yaml"
    monkeypatch.setattr(registry, "overrides_path", lambda: path)
    monkeypatch.setattr(registry, "_speech_config", lambda: {"enabled": False, "tts": {"voice": "af_heart"}})
    registry.reset_engines()
    yield path
    registry.reset_engines()


class TestOverrideFile:
    def test_absent_overrides_leave_config_untouched(self) -> None:
        assert registry._merged_config() == {"enabled": False, "tts": {"voice": "af_heart"}}

    def test_overrides_win_over_config(self, _isolated_overrides) -> None:
        registry.save_overrides({"enabled": True})
        merged = registry._merged_config()
        assert merged["enabled"] is True
        # Untouched keys still come from config.yaml.
        assert merged["tts"] == {"voice": "af_heart"}

    def test_a_section_is_replaced_wholesale_not_deep_merged(self) -> None:
        """A deep merge would let a stale key from config.yaml survive inside a
        section the user just rewrote — invisible state that makes a settings
        panel untrustworthy."""
        registry.save_overrides({"tts": {"device": "cpu"}})
        assert registry._merged_config()["tts"] == {"device": "cpu"}, "the old voice key leaked through"

    def test_clear_restores_config(self) -> None:
        registry.save_overrides({"enabled": True})
        assert registry.is_speech_enabled() is True
        registry.clear_overrides()
        assert registry.is_speech_enabled() is False

    def test_clear_is_safe_when_nothing_was_written(self) -> None:
        registry.clear_overrides()  # must not raise

    def test_corrupt_overrides_do_not_take_voice_down(self, _isolated_overrides) -> None:
        _isolated_overrides.write_text("this: is: not: valid: yaml:\n  - [", encoding="utf-8")
        # Falls back to config.yaml rather than raising.
        assert registry._merged_config() == {"enabled": False, "tts": {"voice": "af_heart"}}

    def test_config_yaml_is_never_written(self, tmp_path, monkeypatch) -> None:
        """The whole reason for a separate file."""
        config = tmp_path / "config.yaml"
        config.write_text("# a comment worth keeping\nspeech:\n  enabled: false\n", encoding="utf-8")
        before = config.read_text(encoding="utf-8")
        registry.save_overrides({"enabled": True})
        assert config.read_text(encoding="utf-8") == before


class TestSavingDropsCachedEngines:
    """The negative test the plan called for: without `reset_engines()` the panel
    silently does nothing."""

    def test_saving_evicts_the_cached_engines(self) -> None:
        registry.set_engines(stt=ScriptedSTT(), tts=ToneTTS())
        assert registry._stt is not None and registry._tts is not None

        registry.save_overrides({"enabled": True})

        assert registry._stt is None, "a stale STT engine survived a settings change"
        assert registry._tts is None, "a stale TTS engine survived a settings change"

    def test_clearing_also_evicts(self) -> None:
        registry.set_engines(stt=ScriptedSTT(), tts=ToneTTS())
        registry.clear_overrides()
        assert registry._stt is None and registry._tts is None

    def test_next_build_uses_the_new_settings(self, monkeypatch) -> None:
        """End to end: change the engine, and the *next* engine really differs."""
        registry.set_engines(tts=ToneTTS())
        assert registry.get_tts_engine().name == "tone"

        registry.save_overrides({"enabled": True, "tts": {"use": "deerflow.speech.engines.null:ToneTTS", "sample_rate": 8000}})
        rebuilt = registry.get_tts_engine()
        assert rebuilt.sample_rate == 8000, "the rebuilt engine ignored the new settings"


class TestConfigEndpoints:
    def _call(self, fn, body=None):
        class _Req:
            async def json(self):
                return body

        impl = getattr(fn, "__wrapped__", fn)
        return asyncio.run(impl(_Req()))

    def test_get_reports_settings_and_catalog(self) -> None:
        from app.gateway.routers import voice as voice_mod

        out = self._call(voice_mod.get_voice_config)
        assert out["settings"]["enabled"] is False
        assert out["has_overrides"] is False
        # The catalog is what the panel renders as choices.
        assert any(e["use"].endswith("FasterWhisperSTT") for e in out["catalog"]["stt"])
        assert any(e["use"].endswith("KokoroTTS") for e in out["catalog"]["tts"])

    def test_put_persists_and_reports_back(self) -> None:
        from app.gateway.routers import voice as voice_mod

        out = self._call(voice_mod.put_voice_config, {"enabled": True})
        assert out["settings"]["enabled"] is True
        assert registry.overrides_path().is_file()

    def test_every_verb_returns_the_same_shape(self) -> None:
        """GET, PUT and DELETE describe the same resource, so they must describe
        it identically.

        They did not: PUT replied `{saved, settings}` with no `catalog`. The
        panel does `setConfig(response)` after saving, so changing any setting
        wiped the catalog and the next render died on `config.catalog.stt` —
        a TypeError in the UI for a purely server-side inconsistency.
        """
        from app.gateway.routers import voice as voice_mod

        # `live` is deliberately conditional — it only exists while voice is
        # enabled and the engines actually loaded — so it is not part of the
        # guaranteed shape. Everything the panel renders unconditionally is.
        required = {"settings", "catalog", "overrides_path", "has_overrides"}

        get_keys = set(self._call(voice_mod.get_voice_config))
        put_keys = set(self._call(voice_mod.put_voice_config, {"enabled": True}))
        delete_keys = set(self._call(voice_mod.reset_voice_config))

        for verb, keys in (("GET", get_keys), ("PUT", put_keys), ("DELETE", delete_keys)):
            assert required <= keys, f"{verb} is missing {required - keys} — the panel renders these without guarding"

    def test_catalog_covers_every_configurable_section(self) -> None:
        """The panel renders a section per catalog key; a missing one silently
        removes the only way to configure that engine from the UI."""
        from app.gateway.routers import voice as voice_mod

        catalog = self._call(voice_mod.get_voice_config)["catalog"]
        assert {"stt", "tts", "turn"} <= set(catalog)
        for section, entries in catalog.items():
            assert entries, f"catalog.{section} is empty — nothing would render"
            for entry in entries:
                assert entry.get("use") and entry.get("label"), f"catalog.{section} entry is missing use/label: {entry}"

    def test_put_rejects_an_unusable_engine_path(self) -> None:
        """A typo must fail at write time with a clear message, not leave voice
        unloadable until someone reads the logs."""
        from fastapi import HTTPException

        from app.gateway.routers import voice as voice_mod

        with pytest.raises(HTTPException) as e:
            self._call(voice_mod.put_voice_config, {"tts": {"use": "deerflow.speech.engines.null:NoSuchEngine"}})
        assert e.value.status_code == 400
        assert "speech.tts.use" in str(e.value.detail)

    def test_put_rejects_a_non_object_body(self) -> None:
        from fastapi import HTTPException

        from app.gateway.routers import voice as voice_mod

        with pytest.raises(HTTPException) as e:
            self._call(voice_mod.put_voice_config, ["not", "an", "object"])
        assert e.value.status_code == 400

    def test_put_invalidates_the_cached_readiness(self) -> None:
        """`/status` caches its answer; after a settings change that answer
        describes engines that no longer exist."""
        from app.gateway.routers import voice as voice_mod

        voice_mod._readiness = {"ready": True, "tts": "stale"}
        self._call(voice_mod.put_voice_config, {"enabled": True})
        assert voice_mod._readiness is None

    def test_delete_resets(self) -> None:
        from app.gateway.routers import voice as voice_mod

        self._call(voice_mod.put_voice_config, {"enabled": True})
        out = self._call(voice_mod.reset_voice_config)
        assert out["settings"]["enabled"] is False
        assert not registry.overrides_path().is_file()

    def test_config_endpoints_are_auth_guarded(self) -> None:
        """These read and write server configuration."""
        from app.gateway.routers import voice as voice_mod

        for fn in (voice_mod.get_voice_config, voice_mod.put_voice_config, voice_mod.reset_voice_config):
            assert hasattr(fn, "__wrapped__"), f"{fn.__name__} is missing @require_auth"
