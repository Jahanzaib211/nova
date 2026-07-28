# AMD Compute Integration

Nova runs its inference on **AMD Instinct** GPUs. This document is both the setup
recipe and the AMD-compute writeup for the AMD Developer Hackathon (Act II).

Nova treats every model provider as a config entry (`use` + `model` +
`base_url` + `api_key`), so wiring AMD compute is configuration, not code.
Two AMD-backed paths ship as one-click presets under **Settings → Models**:

| Preset button | Path | AMD compute |
|---|---|---|
| **Add Fireworks model (AMD MI300X)** | Managed Fireworks AI API | Fireworks serves on AMD Instinct MI300X |
| **Add AMD Instinct model (vLLM/ROCm)** | Nova's own vLLM server on AMD Developer Cloud | Bare-metal AMD Instinct via ROCm |

Both surface an **AMD** badge in the models list, and the
`GET /api/models/amd-usage` endpoint reports AMD usage as a machine-readable
signal for automated pre-screening.

## Path A — Fireworks AI (managed, 15 minutes)

Fireworks hosts models on AMD Instinct MI300X. Nova calls it like any
OpenAI-compatible endpoint.

1. Get a key ($50 hackathon credits) and export it:

   ```bash
   export FIREWORKS_API_KEY=fw-...
   ```

2. Settings → Models → **Add Fireworks model (AMD MI300X)**. The preset fills
   `base_url=https://api.fireworks.ai/inference/v1` and
   `api_key=$FIREWORKS_API_KEY` (resolved from the environment — never persisted
   in plaintext). Name it and Save.
3. Fireworks endpoints are auto-detected as AMD-backed — the AMD badge appears
   with no extra flag.

Gemma models (`accounts/fireworks/models/gemma-3-27b-it`) additionally qualify
for the Gemma side prize.

## Path B — AMD Developer Cloud + vLLM/ROCm (bare-metal)

Nova serves its own inference on an AMD Instinct GPU. This is the strongest
"Use of AMD Platforms" signal because the compute is ours, end to end.

1. Get an AMD Developer Cloud instance: <https://notebooks.amd.com/hackathon>
2. On the instance, serve a model with vLLM on ROCm:

   ```bash
   export VLLM_API_KEY=choose-a-strong-key
   AMD_MODEL=google/gemma-3-27b-it ./scripts/amd-serve-vllm.sh
   ```

   The script confirms ROCm hardware (`rocm-smi`), then runs the official
   `rocm/vllm` image with the `/dev/kfd` + `/dev/dri` passthroughs and exposes an
   OpenAI-compatible API on `:8000`. **Lock port 8000 to your IP** in the cloud
   firewall — it binds `0.0.0.0` because Nova connects over the network.
3. Settings → Models → **Add AMD Instinct model (vLLM/ROCm)**. Replace
   `<amd-droplet-ip>` with the instance address, set the API key, Save. The
   preset carries an explicit `amd_compute` label so the AMD badge shows.
4. Click **Test** on the model row to verify connectivity.

## Verifying AMD usage

```bash
curl -s http://localhost:8001/api/models/amd-usage | jq
```

```json
{
  "amd_backed": true,
  "count": 2,
  "models": [
    { "name": "fireworks-amd", "label": "AMD Instinct MI300X (Fireworks)" },
    { "name": "amd-instinct-vllm", "label": "AMD Instinct MI300X (vLLM/ROCm)" }
  ],
  "summary": "Nova is running on AMD compute: 2 AMD-backed model(s) configured ..."
}
```

## Honesty note

Detection is deliberately conservative (see
`backend/app/gateway/routers/models.py::detect_amd_compute`): only Fireworks
endpoints are auto-claimed as AMD (they are verifiably AMD-hosted). A plain vLLM
server is **not** assumed to be AMD — vLLM also runs on other vendors — so
self-hosted AMD endpoints must opt in with an explicit `amd_compute` label. The
AMD badge never overstates where inference actually ran.

## Hand-config equivalent

The preset buttons are convenience wrappers; the same providers can be added by
hand in `config.yaml` — see the "AMD compute" examples in `config.example.yaml`.
