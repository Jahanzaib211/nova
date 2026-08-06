#!/usr/bin/env bash
# Download the open-source voice model weights.
#
# Nothing here needs an API key and nothing phones home afterwards — these are
# one-time downloads that then run entirely on your machine.
#
#   Kokoro-82M (TTS)  ~310 MB  Apache-2.0
#   Silero VAD        ~2 MB    MIT
#
# faster-whisper fetches its own weights on first use (~75 MB for `base`), so it
# is not downloaded here.
set -euo pipefail

DEST="${DEERFLOW_VOICE_MODEL_DIR:-$HOME/.cache/nova/voice}"
mkdir -p "$DEST"

fetch() {
  local url="$1" out="$2"
  if [ -s "$DEST/$out" ]; then
    echo "  already have $out"
    return
  fi
  echo "  fetching $out"
  curl -fL --progress-bar -o "$DEST/$out.partial" "$url"
  mv "$DEST/$out.partial" "$DEST/$out"   # never leave a truncated file behind
}

# int8 by default: 92 MB vs 325 MB for fp32, materially less resident memory,
# and the quality gap is small for conversational speech. Set
# DEERFLOW_TTS_PRECISION=fp32 for the larger model.
PRECISION="${DEERFLOW_TTS_PRECISION:-int8}"
if [ "$PRECISION" = "fp32" ]; then
  KOKORO_FILE="kokoro-v1.0.onnx"
else
  KOKORO_FILE="kokoro-v1.0.int8.onnx"
fi

echo "Downloading voice models into $DEST  (TTS precision: $PRECISION)"
# Small files first, so VAD and voices are usable while the big one streams.
fetch "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx" "silero_vad.onnx"
fetch "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin" "voices-v1.0.bin"
fetch "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$KOKORO_FILE" "$KOKORO_FILE"

cat <<EOF

Done. Add to your .env:

  DEERFLOW_TTS_MODEL_PATH=$DEST/$KOKORO_FILE
  DEERFLOW_TTS_VOICES_PATH=$DEST/voices-v1.0.bin
  DEERFLOW_VAD_MODEL_PATH=$DEST/silero_vad.onnx

Then set 'speech.enabled: true' in config.yaml and restart the gateway.
EOF
