# Track 3 — Demo Video Script (Nova on AMD)

Target: 2–3 minutes. The automated pre-screen does **not** watch the video; it is
for the human judges. Show, don't tell — every claim on screen is something a
judge can also verify in the repo or live demo.

## Shot list

**0:00–0:15 — Hook**
- Screen: Nova workspace, a task running in the "Agent's Computer".
- VO: "This is Nova — a full-stack AI agent platform. Everything you're about to
  see runs its inference on AMD Instinct compute."

**0:15–0:45 — The product is real**
- Screen: live run — planner, tool calls, terminal + editor + sandboxed browser
  preview updating.
- VO: "Nova plans, uses tools, and drives its own computer — a shipped product,
  not a prototype. The differentiator is underneath: the model layer."

**0:45–1:30 — AMD compute, one click**
- Screen: Settings → Models. Click **Add Fireworks model (AMD MI300X)**; save.
  Then **Add AMD Instinct model (vLLM/ROCm)**; save. Point at the **AMD** badges.
- VO: "Two AMD paths, added as presets. Fireworks serves on AMD Instinct MI300X.
  And Nova serves its own inference with vLLM on ROCm on an AMD Developer Cloud
  instance. Adding AMD compute is configuration, not code."

**1:30–2:00 — Verifiable, not hand-wavy**
- Screen: terminal `curl /api/models/amd-usage | jq` → `amd_backed: true`.
  Then run a task on the AMD Instinct model; show the token counter.
- VO: "AMD usage is a machine-readable signal — the same one the pre-screen
  reads. And we're honest: only verified AMD endpoints are ever claimed."

**2:00–2:30 — Coverage + close**
- Screen: `make hackathon-track1` smoke passing; the deck's "all three tracks"
  slide.
- VO: "The same platform delivers the general-purpose agent track, is Gemma
  side-prize ready, and is our Unicorn: agents, on AMD. Repo and live demo in the
  description."

## Capture checklist
- Have `FIREWORKS_API_KEY` and the AMD Dev Cloud vLLM endpoint live before recording.
- Pre-warm the models (first call is slow) so on-camera runs are snappy.
- Record at 1920×1080; keep the browser at 100% zoom for crisp text.
