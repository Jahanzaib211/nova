# Voice System

Full-duplex speech: talk to Nova, Nova talks back, and you can interrupt it mid-sentence. Everything runs locally — no API key, no per-minute cost, no audio leaving the machine. **Off by default** (`speech.enabled` in `config.yaml`).

## Architecture

```
Browser (AudioWorklet)
  │  raw PCM16 frames
  ▼
WebSocket /api/voice/session/{thread_id}
  │
  ▼
VoiceSession (turn-taking logic)
  ├── SileroVad (ONNX, ~2 MB) — decides when you started/stopped talking
  ├── SmartTurnV3 (optional) — semantic turn detection (prosody-based)
  ├── STT: faster-whisper (Whisper on CTranslate2) — ~4x faster than openai-whisper
  └── TTS: kokoro-onnx (Kokoro-82M, Apache-2.0) — best quality-per-byte in open source
```

## Technology Stack

| Job | Technology | Why |
|---|---|---|
| STT | `faster-whisper` (Whisper on CTranslate2) | ~4x faster and far leaner than `openai-whisper`; int8 is realtime on CPU |
| TTS | `kokoro-onnx` (Kokoro-82M, Apache-2.0) | best quality-per-byte in open source, and the ONNX build means **no PyTorch** |
| VAD | Silero VAD (ONNX, ~2 MB) | decides when you started/stopped talking — this is what makes barge-in possible |

The no-PyTorch constraint is the load-bearing decision: torch would add ~2 GB to the gateway image and a lot of resident memory. `onnxruntime` is shared by Kokoro and Silero.

## Setup

```bash
# Install voice dependencies
cd backend && uv sync --extra voice

# Download voice model weights
scripts/fetch-voice-models.sh

# Enable in config.yaml
# speech:
#   enabled: true
```

### Docker

Voice is an opt-in overlay. `scripts/docker.sh` appends `docker-compose.voice.yaml` only when `speech.enabled: true` **and** the weights exist on disk. GPU acceleration uses a separate `docker-compose.voice-gpu.yaml` overlay (requires `nvidia-smi` + Docker nvidia runtime).

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/voice/session/{thread_id}` | WebSocket | Full-duplex voice session (binary PCM16 up, JSON events + binary PCM16 down) |
| `/api/voice/speak` | POST | One-shot TTS (text in, WAV out) — `@require_auth` gated |
| `/api/voice/config` | GET/PUT/DELETE | Voice settings CRUD (backed by `voice-settings.yaml`) |
| `/api/voice/transcribe` | POST | Microphone test (WAV PCM16 only) |

## Performance

| Kokoro model | Device | RTF | First audio |
|---|---|---|---|
| fp32 | cpu | 0.392x | 510 ms |
| fp32 | cuda | 0.154x | 200 ms |
| int8 | cpu | 2.163x | 2815 ms |
| int8 | cuda | 2.156x | 2851 ms |

**Default is fp32 everywhere.** int8 is 5.5x slower than fp32 on the same CPU.

Whisper on CUDA: RTF 0.632 → 0.021 (~30x).

## Configuration

```yaml
speech:
  enabled: true
  stt:
    engine: deerflow.speech.engines.faster_whisper:FasterWhisperSTT
    model_size: small
    device: auto  # auto|cuda|cpu
  tts:
    engine: deerflow.speech.engines.kokoro:KokoroTTS
    voice: af_heart
    device: auto
  vad:
    engine: deerflow.speech.engines.silero:SileroVad
    threshold: 0.5
  turn_detection:
    enabled: false
    engine: deerflow.speech.engines.smart_turn:SmartTurnV3
```

Settings are stored in `$DEER_FLOW_HOME/voice-settings.yaml` (overrides `config.yaml`).

## Troubleshooting

### Microphone not working

- Check nginx headers: `Permissions-Policy: microphone=(self)` must be set
- WebSocket path needs its own nginx `location` with `Upgrade` headers
- CSP must include `media-src 'self' blob: data:` for audio playback

### Voice sounds robotic or slow

- Ensure fp32 weights are used (not int8): check `voice-settings.yaml`
- On CPU, fp32 is faster than int8 — do not use int8 to save disk

### STT not transcribing

- Check `LD_LIBRARY_PATH` for CUDA libraries (or use `preload_cuda_libraries()`)
- Verify with: `POST /api/voice/transcribe` (microphone test)

### VAD too sensitive / not sensitive enough

- Adjust `speech.vad.threshold` in config (0.0-1.0, default 0.5)
- Ensure 512-sample window with 64-sample context prefix (Silero contract)

## Key Files

- `backend/packages/harness/deerflow/speech/` — Speech engines (STT, TTS, VAD, turn detection)
- `backend/app/gateway/routers/voice.py` — Voice WebSocket and REST endpoints
- `backend/tests/test_voice_session.py` — Voice session unit tests
- `frontend/src/core/voice/` — Browser-side voice capture and playback
- `frontend/src/features/voice/pages/voice-settings-page.tsx` — Voice settings UI (feature module; i18n under `t.features.voice`)
