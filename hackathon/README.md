# Nova × AMD Developer Hackathon (Act II)

Nova competes as one product across the hackathon. The engineering core is a
first-class **AMD compute integration**; the tracks are thin wrappers over
Nova's existing agent, provider system, and token telemetry.

| Item | Where |
|---|---|
| AMD compute setup + writeup | [`docs/AMD_INTEGRATION.md`](../docs/AMD_INTEGRATION.md) |
| AMD vLLM/ROCm serving script | [`scripts/amd-serve-vllm.sh`](../scripts/amd-serve-vllm.sh) |
| AMD usage endpoint | `GET /api/models/amd-usage` (`backend/app/gateway/routers/models.py`) |
| **Track 1** — general-purpose agent | [`track1/`](./track1/) · `make hackathon-track1` |
| **Track 3** — Unicorn (flagship) | [`track3/`](./track3/) |

## Automation

```bash
make hackathon-track1            # build (linux/amd64) + smoke test
make hackathon-track1-submit REGISTRY=ghcr.io/<org>   # smoke → push → verify <10GB
make hackathon-track3-deck       # render the slide deck to PDF
make hackathon-track3-prescreen  # self-audit repo (+ live demo if NOVA_LIVE_URL set)
```
