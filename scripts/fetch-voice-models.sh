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

# How many range requests to run at once.
#
# This is not premature optimization. GitHub's release CDN throttles a single
# long-lived connection hard — an observed download decayed to ~19 KB/s on a
# 160 Mbit link, which turns the 92 MB model into a 90-minute wait. The same
# host serves ~470 KB/s aggregate across parallel connections. Splitting the
# file is the difference between three minutes and an afternoon.
JOBS="${DEERFLOW_FETCH_JOBS:-8}"
# Below this, connection setup costs more than the parallelism saves.
PARALLEL_MIN_BYTES=$((4 * 1024 * 1024))

remote_size() {
  # -L because these are redirects to a CDN; the last Content-Length wins, since
  # the redirect hops themselves report 0.
  curl -fsIL --max-time 30 "$1" 2>/dev/null \
    | tr -d '\r' \
    | awk 'tolower($1) == "content-length:" && $2 > 0 { n = $2 } END { print n + 0 }'
}

# Download one byte range, resuming whatever is already on disk.
#
# `curl -C -` cannot be combined with `-r`: -C sets its own Range header and the
# two collide. So the resume offset is computed here and the part is appended.
fetch_range() {
  local url="$1" part="$2" start="$3" end="$4"
  local have=0
  [ -f "$part" ] && have=$(stat -c %s "$part")
  local want=$((end - start + 1))
  [ "$have" -ge "$want" ] && return 0
  curl -fsL --retry 5 --retry-delay 2 --retry-all-errors \
    -r "$((start + have))-$end" "$url" >> "$part"
}

fetch_parallel() {
  local url="$1" out="$2" size="$3"
  local chunk=$(((size + JOBS - 1) / JOBS))
  local pids=() i start end rc=0

  for ((i = 0; i < JOBS; i++)); do
    start=$((i * chunk))
    [ "$start" -ge "$size" ] && break
    end=$((start + chunk - 1))
    [ "$end" -ge "$size" ] && end=$((size - 1))
    # Zero-padded: the reassembly below relies on glob order, and `chunk10`
    # sorts before `chunk2`. Unpadded names would scramble the file in a way
    # the size check cannot detect.
    fetch_range "$url" "$(printf '%s/%s.chunk%03d' "$DEST" "$out" "$i")" "$start" "$end" &
    pids+=($!)
  done
  for pid in "${pids[@]}"; do wait "$pid" || rc=1; done
  if [ "$rc" -ne 0 ]; then
    echo "  a chunk failed — re-run to resume" >&2
    return 1
  fi

  cat "$DEST/$out".chunk* > "$DEST/$out.partial"
  local got
  got=$(stat -c %s "$DEST/$out.partial")
  if [ "$got" -ne "$size" ]; then
    # Truncated or overlapping parts would produce a corrupt ONNX file that
    # fails deep inside onnxruntime with an unreadable error. Fail loudly here.
    echo "  size mismatch for $out: got $got, expected $size" >&2
    rm -f "$DEST/$out.partial"
    return 1
  fi
  rm -f "$DEST/$out".chunk*
}

fetch() {
  local url="$1" out="$2"
  if [ -s "$DEST/$out" ]; then
    echo "  already have $out"
    return
  fi
  local size
  size=$(remote_size "$url")
  if [ "$size" -ge "$PARALLEL_MIN_BYTES" ]; then
    echo "  fetching $out ($((size / 1024 / 1024)) MB, ${JOBS} parallel ranges)"
    fetch_parallel "$url" "$out" "$size"
  else
    echo "  fetching $out"
    curl -fL --progress-bar --retry 5 --retry-all-errors -o "$DEST/$out.partial" "$url"
  fi
  mv "$DEST/$out.partial" "$DEST/$out"   # never leave a truncated file behind
}

# fp32 by default, despite being 311 MB against int8's 89 MB.
#
# This is measured, not assumed. The int8 build is **5.5x slower than fp32 on
# the same CPU** — the opposite of what quantization is supposed to buy:
#
#   model  device   RTF     first-audio
#   int8   cpu      2.163x  2815 ms
#   int8   cuda     2.156x  2851 ms   <- barely helped; ~547 Memcpy nodes are
#                                        inserted because most int8 ops have no
#                                        CUDA kernel and bounce back to the host
#   fp32   cpu      0.392x   510 ms
#   fp32   cuda     0.154x   200 ms
#
# So int8 was costing 5.5x throughput to save 222 MB of disk, and it made the
# GPU nearly useless as well. fp32 is faster than real time even on CPU, which
# matters for the k3s deployment that has no GPU at all.
#
# Set DEERFLOW_TTS_PRECISION=int8 if disk is genuinely the binding constraint.
PRECISION="${DEERFLOW_TTS_PRECISION:-fp32}"
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
