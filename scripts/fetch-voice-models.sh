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

echo "Downloading voice models into $DEST"
fetch "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx" "kokoro-v1.0.onnx"
fetch "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin" "voices-v1.0.bin"
fetch "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx" "silero_vad.onnx"

cat <<EOF

Done. Add to your .env:

  DEERFLOW_TTS_MODEL_PATH=$DEST/kokoro-v1.0.onnx
  DEERFLOW_TTS_VOICES_PATH=$DEST/voices-v1.0.bin
  DEERFLOW_VAD_MODEL_PATH=$DEST/silero_vad.onnx

Then set 'speech.enabled: true' in config.yaml and restart the gateway.
EOF
