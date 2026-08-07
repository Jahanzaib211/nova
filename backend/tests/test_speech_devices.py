"""Device placement for the speech engines.

Nova serves two hosts from one `config.yaml`: a workstation with an RTX 3060 and
a k3s node with no GPU. `device: "auto"` is what makes that single config
correct on both, so the fallback behaviour is load-bearing rather than
cosmetic — and it is exactly the kind of thing that fails silently.

None of this needs a GPU, weights, or onnxruntime-gpu: availability is stubbed,
so these run identically in CI and on the workstation.
"""

from __future__ import annotations

import logging

import pytest

from deerflow.speech import devices


@pytest.fixture(autouse=True)
def _forget_warnings():
    devices.reset_warnings()
    yield
    devices.reset_warnings()


class TestOnnxProviderResolution:
    def test_cpu_never_touches_the_gpu(self, monkeypatch) -> None:
        monkeypatch.setattr(devices, "cuda_available_onnx", lambda: True)
        providers, resolved = devices.resolve_onnx_providers("cpu")
        assert providers == [devices.CPU_PROVIDER]
        assert resolved == "cpu"

    def test_auto_uses_cuda_when_available(self, monkeypatch) -> None:
        monkeypatch.setattr(devices, "cuda_available_onnx", lambda: True)
        providers, resolved = devices.resolve_onnx_providers("auto")
        assert providers[0] == devices.CUDA_PROVIDER
        assert resolved == "cuda"

    def test_cpu_is_always_the_last_provider(self, monkeypatch) -> None:
        """onnxruntime falls back per-op, so an op CUDA cannot handle still runs
        instead of failing the whole session."""
        monkeypatch.setattr(devices, "cuda_available_onnx", lambda: True)
        providers, _ = devices.resolve_onnx_providers("auto")
        assert providers[-1] == devices.CPU_PROVIDER

    def test_auto_falls_back_quietly(self, monkeypatch, caplog) -> None:
        """On a CPU-only box, auto choosing CPU is a decision, not a problem —
        k3s must not log a warning on every boot."""
        monkeypatch.setattr(devices, "cuda_available_onnx", lambda: False)
        with caplog.at_level(logging.WARNING):
            providers, resolved = devices.resolve_onnx_providers("auto")
        assert resolved == "cpu"
        assert providers == [devices.CPU_PROVIDER]
        assert not caplog.records, "auto on a CPU-only host must not warn"

    def test_explicit_cuda_falls_back_loudly(self, monkeypatch, caplog) -> None:
        """Asking for CUDA and silently getting CPU is the failure mode that hid
        the Silero bug: it still 'works', just badly, with nothing saying why."""
        monkeypatch.setattr(devices, "cuda_available_onnx", lambda: False)
        with caplog.at_level(logging.WARNING):
            _, resolved = devices.resolve_onnx_providers("cuda")
        assert resolved == "cpu"
        assert any("cuda" in r.message.lower() for r in caplog.records), "the fallback was silent"

    def test_warns_once_not_per_call(self, monkeypatch, caplog) -> None:
        """This is consulted per session; a per-call warning would bury itself."""
        monkeypatch.setattr(devices, "cuda_available_onnx", lambda: False)
        with caplog.at_level(logging.WARNING):
            for _ in range(20):
                devices.resolve_onnx_providers("cuda", subsystem="same")
        assert len(caplog.records) == 1, f"expected one warning, got {len(caplog.records)}"

    def test_unknown_device_degrades_to_auto(self, monkeypatch, caplog) -> None:
        monkeypatch.setattr(devices, "cuda_available_onnx", lambda: False)
        with caplog.at_level(logging.WARNING):
            _, resolved = devices.resolve_onnx_providers("gpu")  # not a valid value
        assert resolved == "cpu"
        assert caplog.records, "a typo in config should be reported, not swallowed"

    def test_missing_onnxruntime_is_not_fatal(self, monkeypatch) -> None:
        monkeypatch.setitem(__import__("sys").modules, "onnxruntime", None)
        assert devices.cuda_available_onnx() is False


class TestCudaPreload:
    """The pip `nvidia-*-cu12` wheels put CUDA under site-packages/nvidia, which
    is not on the linker's search path. `LD_LIBRARY_PATH` cannot fix this from
    inside Python — the linker reads it at exec — so the libraries are loaded
    with RTLD_GLOBAL instead, and every later dlopen by SONAME resolves to them.
    """

    def test_is_idempotent(self, monkeypatch) -> None:
        monkeypatch.setattr(devices, "_preloaded", False)
        devices.preload_cuda_libraries()
        # Second call must be a no-op, not a second round of CDLL calls.
        assert devices.preload_cuda_libraries() == devices._preloaded

    def test_no_nvidia_directory_is_not_an_error(self, monkeypatch, tmp_path) -> None:
        """A CPU-only host (k3s) has no such directory and must start normally.

        The invariant is *no exception*, not a particular return value: with
        onnxruntime-gpu installed, `preload_dlls()` can legitimately succeed
        before the ctypes pass ever looks for the directory.
        """
        import sysconfig

        monkeypatch.setattr(devices, "_preloaded", False)
        monkeypatch.setattr(sysconfig, "get_paths", lambda: {"purelib": str(tmp_path)})
        assert devices.preload_cuda_libraries() in (True, False)  # must not raise

    def test_returns_false_when_nothing_can_be_preloaded(self, monkeypatch, tmp_path) -> None:
        """The genuinely bare case: no onnxruntime helper and no wheels."""
        import sys
        import sysconfig

        monkeypatch.setattr(devices, "_preloaded", False)
        monkeypatch.setattr(sysconfig, "get_paths", lambda: {"purelib": str(tmp_path)})
        monkeypatch.setitem(sys.modules, "onnxruntime", None)
        assert devices.preload_cuda_libraries() is False

    def test_unloadable_library_does_not_raise(self, monkeypatch, tmp_path) -> None:
        """A corrupt or ABI-mismatched .so must degrade to CPU, not crash the
        gateway on import."""
        import sysconfig

        lib_dir = tmp_path / "nvidia" / "cublas" / "lib"
        lib_dir.mkdir(parents=True)
        (lib_dir / "libcublas.so.12").write_bytes(b"not an elf file")

        monkeypatch.setattr(devices, "_preloaded", False)
        monkeypatch.setattr(sysconfig, "get_paths", lambda: {"purelib": str(tmp_path)})
        assert devices.preload_cuda_libraries() is False  # nothing loaded, no exception


class TestCTranslate2DeviceResolution:
    def test_auto_uses_cuda_when_available(self, monkeypatch) -> None:
        monkeypatch.setattr(devices, "cuda_available_ct2", lambda: True)
        assert devices.resolve_ct2_device("auto") == "cuda"

    def test_auto_falls_back_to_cpu(self, monkeypatch) -> None:
        monkeypatch.setattr(devices, "cuda_available_ct2", lambda: False)
        assert devices.resolve_ct2_device("auto") == "cpu"

    def test_explicit_cuda_warns_on_fallback(self, monkeypatch, caplog) -> None:
        monkeypatch.setattr(devices, "cuda_available_ct2", lambda: False)
        with caplog.at_level(logging.WARNING):
            assert devices.resolve_ct2_device("cuda") == "cpu"
        assert caplog.records


class TestComputeTypeFollowsDevice:
    def test_int8_on_cpu(self) -> None:
        """int8 is a concession to CPU speed and costs accuracy."""
        assert devices.default_compute_type("cpu") == "int8"

    def test_float16_on_cuda(self) -> None:
        """On a GPU the int8 trade stops making sense."""
        assert devices.default_compute_type("cuda") == "float16"

    def test_explicit_choice_always_wins(self) -> None:
        assert devices.default_compute_type("cuda", "int8_float16") == "int8_float16"
        assert devices.default_compute_type("cpu", "float32") == "float32"


class TestHalfCudaSessionIsRejected:
    """onnxruntime accepts CUDAExecutionProvider, then silently drops it at
    session creation when its CUDA runtime is missing or version-mismatched.

    Keeping that session is the *worst* outcome: the graph is still partitioned
    for CUDA, hundreds of Memcpy nodes get inserted, and it measured **slower
    than plain CPU** — RTF 2.18 against 0.398 in the gateway container. So the
    engine rebuilds CPU-only rather than living in the broken middle, and
    reports the device it actually got.
    """

    @staticmethod
    def _kokoro_with_fake_ort(monkeypatch, granted_providers):
        import sys
        import types

        from deerflow.speech.engines import kokoro_tts

        built: list[list[str]] = []

        class FakeSession:
            def __init__(self, _path, sess_options=None, providers=None):
                built.append(list(providers or []))
                self._granted = granted_providers(providers or [])

            def get_providers(self):
                return self._granted

        fake_ort = types.ModuleType("onnxruntime")
        fake_ort.InferenceSession = FakeSession
        fake_ort.SessionOptions = lambda: object()
        fake_ort.get_available_providers = lambda: [devices.CUDA_PROVIDER, devices.CPU_PROVIDER]
        monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)

        engine = kokoro_tts.KokoroTTS(model_path="/model.onnx", voices_path="/voices.bin", device="cuda")
        return engine, built

    def test_rebuilds_cpu_only_when_cuda_is_dropped(self, monkeypatch) -> None:
        engine, built = self._kokoro_with_fake_ort(monkeypatch, lambda _req: [devices.CPU_PROVIDER])
        engine._build_session()

        assert len(built) == 2, "the half-CUDA session was kept instead of being rebuilt"
        assert built[1] == [devices.CPU_PROVIDER], f"rebuild did not drop CUDA: {built[1]}"
        assert engine.resolved_device == "cpu", "reported cuda while running on cpu"

    def test_keeps_the_session_when_cuda_really_works(self, monkeypatch) -> None:
        engine, built = self._kokoro_with_fake_ort(monkeypatch, lambda req: list(req))
        engine._build_session()

        assert len(built) == 1, "rebuilt a session that was working fine"
        assert engine.resolved_device == "cuda"


class TestEnginesAcceptDevice:
    """The registry does `engine_cls(**cfg)`, so a config key that the engine
    does not accept raises at construction. These pin the constructor surface
    the settings panel will write to."""

    def test_kokoro_accepts_device(self) -> None:
        from deerflow.speech.engines.kokoro_tts import KokoroTTS

        engine = KokoroTTS(device="cpu", model_path="/nope.onnx", voices_path="/nope.bin")
        assert engine.device == "cpu"
        assert engine.resolved_device == "cpu"

    def test_whisper_accepts_device(self) -> None:
        from deerflow.speech.engines.faster_whisper_stt import FasterWhisperSTT

        engine = FasterWhisperSTT(device="cuda", model="base")
        assert engine.device == "cuda"
        # Not resolved until load — CTranslate2 can see a device and still fail
        # to load on it, so the honest answer needs an actual attempt.
        assert engine.resolved_device == "cpu"

    def test_whisper_defers_compute_type_to_the_device(self, monkeypatch) -> None:
        from deerflow.speech.engines.faster_whisper_stt import FasterWhisperSTT

        # Explicitly unset: these constructors read env as a fallback, and a
        # developer with DEERFLOW_STT_COMPUTE exported would otherwise see this
        # pass or fail depending on their shell.
        monkeypatch.delenv("DEERFLOW_STT_COMPUTE", raising=False)
        monkeypatch.delenv("DEERFLOW_STT_DEVICE", raising=False)

        assert FasterWhisperSTT()._configured_compute is None
        assert FasterWhisperSTT(compute_type="float32")._configured_compute == "float32"

    def test_whisper_device_defaults_to_auto(self, monkeypatch) -> None:
        from deerflow.speech.engines.faster_whisper_stt import FasterWhisperSTT

        monkeypatch.delenv("DEERFLOW_STT_DEVICE", raising=False)
        assert FasterWhisperSTT().device == "auto", "auto is what makes one config work on GPU and k3s alike"

    def test_kokoro_device_defaults_to_auto(self, monkeypatch) -> None:
        from deerflow.speech.engines.kokoro_tts import KokoroTTS

        monkeypatch.delenv("DEERFLOW_TTS_DEVICE", raising=False)
        assert KokoroTTS(model_path="/nope.onnx", voices_path="/nope.bin").device == "auto"
