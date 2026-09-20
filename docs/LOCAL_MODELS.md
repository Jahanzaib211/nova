> **Status 2026-09-13 — superseded.** The Ollama-served local models described
> below are retired and the Ollama snap is disabled. The single on-device model
> is now `qwen3.8-27b-cyber` (Qwen3.8-27B, llama.cpp, port 8086, 131k ctx, tool
> calling), exposed to Nova as `local-qwen3.8-27b-cyber` in `config.yaml` and as
> LiteLLM route `qwen3.8-27b-cyber`. Source of truth: `~/Desktop/llm-lab/`
> (README.md, NOVA.md, BENCHMARKS.md). The `:cloud` free routes need Ollama and
> are down until it is re-enabled per `llm-lab/OLLAMA.md`.

# Local models

Nova can answer from models running on this machine's GPU, with no prompt
leaving the box. This page records what was measured on the Nova host and how
the numbers translate into configuration.

## Topology

Local models reuse the path the free cloud models already take — no new
plumbing:

```
Nova gateway (docker)
  -> host.docker.internal:4000        nova-litellm (docker/litellm/config.yaml)
    -> 127.0.0.1:11434                Ollama daemon
      -> :cloud tag  -> ollama.com    (leaves the box)
      -> no tag      -> local GGUF    (stays on the RTX 3060)
```

The tag is the only difference. `qwen3-coder-480b-free` is remote;
`qwen2.5-7b-local` is not.

> `local-llm`, an older runtime entry, points at `host.docker.internal:8081`.
> Nothing LLM-shaped listens there — port 8081 belongs to the `apex-hyperswitch`
> payments router, which answers with `{"error":"Unrecognized request URL"}`.
> The entry is `hidden: true` so it never reaches the picker, but it is dead
> config and safe to delete.

## Benchmark

Host: NVIDIA RTX 3060 (12 GB), Ollama 0.32.14, all weights Q4_K_M, f16 KV cache.
Each row loads the model alone, fills ~45% of the window, and generates 128
tokens. `GPU%` is `size_vram / size` from `/api/ps` — the share of the model
that stayed on the card.

| Model | Context | Prefill tok/s | Decode tok/s | GPU | VRAM |
|---|---:|---:|---:|---:|---:|
| qwen2.5:7b-instruct | 8 192 | 2 093 | 61.9 | 100% | 5.13 GB |
| qwen2.5:7b-instruct | 16 384 | 2 126 | 61.7 | 100% | 5.62 GB |
| **qwen2.5:7b-instruct** | **32 768** | **1 955** | **56.0** | **100%** | **6.59 GB** |
| qwen2.5vl:7b | 8 192 | 2 182 | 62.0 | 100% | 5.87 GB |
| qwen2.5vl:7b | 16 384 | 2 125 | 60.2 | 100% | 6.36 GB |
| qwen2.5vl:7b | 32 768 | 1 966 | 52.7 | 100% | 7.33 GB |
| **qwen2.5vl:7b** | **65 536** | **1 575** | **45.5** | **100%** | **9.07 GB** |
| qwen2.5vl:7b | 81 920 | 1 398 | 16.9 | 90.5% | 9.78 GB |
| hymt7b | 8 192 | 1 867 | 42.8 | 100% | 5.81 GB |
| hymt7b | 16 384 | 1 917 | 52.7 | 100% | 7.02 GB |
| **hymt7b** | **32 768** | **1 708** | **49.7** | **100%** | **9.06 GB** |
| hymt7b | 65 536 | 958 | 2.8 | 75.8% | 10.36 GB |

Bold rows are the configured settings: the largest window that still held
100% on the GPU.

### The cliff is the whole story

Throughput decays gently while the model fits and collapses when it does not.
hymt7b doubling 32 768 -> 65 536 costs **17x decode speed** (49.7 -> 2.8 tok/s)
because 24% of the model spills to system RAM. qwen2.5vl shows the same shape
one step later (45.5 -> 16.9 tok/s at 81 920).

There is no gradual warning. A window one step too large does not run "a bit
slower" — it stops being usable. This is why the context number is measured
rather than guessed, and why the UI field is documented as the window the
server was *actually started with*.

### Why the ceilings differ

KV cache per token is `2 x layers x kv_heads x head_dim x 2 bytes`:

| Model | Layers | KV heads | Per token | At 32 768 |
|---|---:|---:|---:|---:|
| qwen2.5 / qwen2.5vl | 28 | 4 | 56 KB | 1.8 GB |
| hymt7b | 32 | 8 | 128 KB | 4.2 GB |

hymt7b advertises a 262 144 context and its weights are the smallest of the
three, but it spends KV cache 2.3x faster — so it tops out at 32 768 here while
qwen2.5vl reaches 65 536. Advertised context says nothing about usable context
on a given card.

## Configuration

Two numbers must agree, or summarization sizes itself against a window that
does not exist:

- `num_ctx` in [docker/litellm/config.yaml](../docker/litellm/config.yaml) —
  what the model is loaded with.
- **Max context** in Settings -> Models — what Nova believes it has.

Raising either past the benchmarked value does not enlarge the window; it
buys the cliff above.

## Adding one from the UI

Settings -> Models -> Add model:

| Field | Value |
|---|---|
| Model ID | the `model_name` from the litellm config |
| Base URL | `http://host.docker.internal:4000/v1` |
| API key | `not-needed` |
| Max context | the benchmarked window |
| Show in chat | on |

**Show in chat** is what puts the model in the chat picker; turning it off
keeps the model configured and usable but out of the dropdown.

## Re-running the benchmark

Hardware, driver, and Ollama version all move these numbers. After changing
any of them, re-measure rather than trusting this table — the useful signal is
the last context with `GPU 100%`.
