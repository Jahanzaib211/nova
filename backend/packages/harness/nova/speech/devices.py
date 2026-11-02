"""Where each speech engine runs, decided in one place.

Nova has to serve two very different hosts from one config: a workstation with
an RTX 3060, and a k3s node with no GPU at all. `device: "auto"` is what makes a
single `config.yaml` correct on both.

The rules are deliberately asymmetric:

- ``auto`` — use the GPU when it is genuinely available, CPU otherwise. This is
  a *choice*, not a failure, so it is logged at info level.
- ``cuda`` — the operator asked for the GPU explicitly. If it is not there we
  still fall back rather than refuse to start (a voice-less deployment is worse
  than a slow one), but we say so at **warning** level, once. Silent degradation
  is exactly what hid the Silero bug: endpointing kept "working", just badly,
  with nothing anywhere saying why.
- ``cpu`` — never touch the GPU.

Two engines, two runtimes: Kokoro and the VAD run on onnxruntime, faster-whisper
runs on CTranslate2. They need different answers, so there is a resolver for
each, but the fallback semantics above are shared.
"""

from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)

Device = Literal["auto", "cuda", "cpu"]

CUDA_PROVIDER = "CUDAExecutionProvider"
CPU_PROVIDER = "CPUExecutionProvider"

# Warn once per process per subsystem, not once per inference. A per-frame
# warning at 50 fps would bury the log it is meant to draw attention to.
_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(message)


def cuda_available_onnx() -> bool:
    """Is a CUDA execution provider actually usable by onnxruntime?

    Note this asks onnxruntime, not the driver. The stock ``onnxruntime`` wheel
    is CPU-only and reports no CUDA provider even on a machine with a working
    GPU — installing ``onnxruntime-gpu`` is what changes this answer.
    """
    try:
        import onnxruntime as ort

        return CUDA_PROVIDER in ort.get_available_providers()
    except Exception:
        return False


def resolve_onnx_providers(device: str = "auto", *, subsystem: str = "onnx") -> tuple[list[str], str]:
    """Pick onnxruntime providers. Returns ``(providers, resolved_device)``.

    CPU is always appended as the last provider: onnxruntime falls back per-op,
    so an op the CUDA provider cannot handle still runs instead of failing the
    whole session.
    """
    want = (device or "auto").strip().lower()
    if want not in ("auto", "cuda", "cpu"):
        _warn_once(f"{subsystem}:bad-device", f"{subsystem}: unknown device {device!r}; using auto")
        want = "auto"

    if want == "cpu":
        return [CPU_PROVIDER], "cpu"

    # Must happen before a CUDA session is created. Without it onnxruntime
    # advertises CUDAExecutionProvider, accepts the session, and then fails at
    # the first conv layer with "cuDNN is unavailable" — a provider being
    # *listed* is not evidence its libraries can be opened.
    preload_cuda_libraries()

    if cuda_available_onnx():
        logger.info("%s: using CUDA", subsystem)
        return [CUDA_PROVIDER, CPU_PROVIDER], "cuda"

    if want == "cuda":
        _warn_once(
            f"{subsystem}:no-cuda",
            f"{subsystem}: device='cuda' was requested but onnxruntime exposes no {CUDA_PROVIDER} (the CPU-only 'onnxruntime' wheel is probably installed instead of 'onnxruntime-gpu'). Falling back to CPU — speech will work but stay slow.",
        )
    return [CPU_PROVIDER], "cpu"


_preloaded = False


def preload_cuda_libraries() -> bool:
    """Make the pip-installed CUDA runtime findable, without `LD_LIBRARY_PATH`.

    CTranslate2 links `libcublas.so.12` and cuDNN 9 by SONAME and finds them via
    the dynamic linker. The `nvidia-*-cu12` wheels install them under
    `site-packages/nvidia/*/lib`, which is not on the default search path — so
    the usual advice is to export `LD_LIBRARY_PATH`. That does not work here:
    the linker reads it at *exec* time, so a Python process cannot set it for
    itself, and every entry point (uvicorn, pytest, the container CMD) would
    have to remember.

    Loading each library with `RTLD_GLOBAL` first has the same effect and needs
    no environment at all: once resident under its SONAME, later `dlopen` calls
    by CTranslate2 or onnxruntime resolve to the already-loaded copy.

    Returns True if anything was loaded. Safe to call repeatedly, and safe on a
    machine with no GPU — a missing directory just means nothing to preload.
    """
    global _preloaded
    if _preloaded:
        return True

    import ctypes
    import sysconfig
    from pathlib import Path

    # onnxruntime >= 1.21 ships a helper built for exactly this layout, and it
    # is strictly better than the ctypes fallback below: onnxruntime dlopens
    # cuDNN by its *unversioned* name (`libcudnn.so`), which an already-loaded
    # `libcudnn.so.9` does not satisfy — dlopen resolves by filename, not by
    # what happens to be resident. preload_dlls() knows that and handles it.
    try:
        import onnxruntime as ort

        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls()
            _preloaded = True
            logger.info("preloaded CUDA libraries via onnxruntime.preload_dlls()")
    except Exception:
        logger.debug("onnxruntime.preload_dlls() unavailable", exc_info=True)

    # CTranslate2 has no such helper and links cuBLAS by SONAME, so it still
    # needs the RTLD_GLOBAL pass below even when preload_dlls() succeeded.
    site = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
    if not site.is_dir():
        return _preloaded

    loaded = 0
    # cuBLAS before cuDNN: cuDNN links against it, and loading in dependency
    # order avoids a resolution failure on the first import.
    for pattern in ("cublas/lib/libcublasLt.so.*", "cublas/lib/libcublas.so.*", "cudnn/lib/libcudnn*.so.*"):
        for lib in sorted(site.glob(pattern)):
            try:
                ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
                loaded += 1
            except OSError:
                # One unloadable library is not fatal: CTranslate2 will fall
                # back to CPU, which resolve_ct2_device already reports.
                logger.debug("could not preload %s", lib.name, exc_info=True)

    _preloaded = loaded > 0
    if _preloaded:
        logger.info("preloaded %d CUDA libraries from %s", loaded, site)
    return _preloaded


def cuda_available_ct2() -> bool:
    """Is CUDA usable by CTranslate2 (faster-whisper's runtime)?

    ``get_cuda_device_count()`` only reports what the *driver* exposes; the
    CUDA runtime libraries CTranslate2 links against can still be missing, which
    fails later at model-load time with a message about ``libcublas``. So the
    count is treated as necessary, not sufficient — the real proof is a
    successful load, which the caller handles.
    """
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def resolve_ct2_device(device: str = "auto", *, subsystem: str = "faster-whisper") -> str:
    """Pick a CTranslate2 device string: ``"cuda"`` or ``"cpu"``."""
    want = (device or "auto").strip().lower()
    if want not in ("auto", "cuda", "cpu"):
        _warn_once(f"{subsystem}:bad-device", f"{subsystem}: unknown device {device!r}; using auto")
        want = "auto"

    if want == "cpu":
        return "cpu"

    # Must happen before CTranslate2 tries to open its CUDA libraries.
    preload_cuda_libraries()

    if cuda_available_ct2():
        return "cuda"

    if want == "cuda":
        _warn_once(
            f"{subsystem}:no-cuda",
            f"{subsystem}: device='cuda' was requested but CTranslate2 sees no CUDA device. Falling back to CPU.",
        )
    return "cpu"


def default_compute_type(resolved_device: str, configured: str | None = None) -> str:
    """Pick a CTranslate2 compute type to match the device.

    ``int8`` exists to make CPU inference bearable and costs accuracy. On a GPU
    that trade stops making sense, so an explicit choice is honoured but the
    *default* follows the device: float16 on CUDA, int8 on CPU.
    """
    if configured:
        return configured
    return "float16" if resolved_device == "cuda" else "int8"


def reset_warnings() -> None:
    """Test hook: forget which warnings have already been emitted."""
    _warned.clear()
