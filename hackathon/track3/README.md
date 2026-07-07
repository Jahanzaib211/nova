# Track 3 — Unicorn (Open Innovation)

**Nova: a full-stack AI agent platform that runs its inference on AMD Instinct compute.**

## Submission deliverables

| Required | Item | Status |
|---|---|---|
| Yes | GitHub repository URL | this repo (make public before submitting) |
| Yes | Slide deck (PDF) | [`deck.pdf`](./deck.pdf) — source [`deck.html`](./deck.html), rebuild with `make hackathon-track3-deck` |
| Yes | Demo video | record from [`demo-video-script.md`](./demo-video-script.md) |
| Recommended | Live demo / hosted URL | deploy Nova; set `NOVA_LIVE_URL` and run the pre-screen |

> The automated pre-screen inspects the **repo, the PDF deck, and the live URL** —
> not the video. Run `make hackathon-track3-prescreen` to self-audit those.

## The AMD-compute story (the judging gate)

AMD compute usage is required or the entry is disqualified. Nova demonstrates it
two ways, both documented in [`docs/AMD_INTEGRATION.md`](../../docs/AMD_INTEGRATION.md):

1. **Fireworks AI** — managed inference on AMD Instinct MI300X.
2. **AMD Developer Cloud** — Nova serves its own inference via vLLM on ROCm.

Verify at any time: `curl <live-url>/api/models/amd-usage` → `"amd_backed": true`.

## Before submitting

```bash
make hackathon-track3-deck                        # (re)build the PDF
NOVA_LIVE_URL=https://<your-demo> \
  make hackathon-track3-prescreen                 # all checks green
```
