# Freja — Shared Task Board

Coordination queue for **Claude Code** and **Antigravity**.
Protocol: [`.agents/COLLABORATION.md`](COLLABORATION.md). Each task has exactly one owner.

> **How to use:** at session start, `git pull`, find the highest-priority task where
> `Owner == you` and `Status ∈ {todo, review}`, set it `in-progress`, work it, then set
> `done` or hand off. Commit & push after every change to this file.

Status: `todo · in-progress · review · blocked · done` · Priority: `P1 · P2 · P3`

---

## Active

### [T-002] Client: show which LLM provider answered the daily check-in
- Owner: antigravity
- Status: blocked
- Priority: P2
- Created-by: anders
- Files: `client/app.js`, `client/index.html`, `client/style.css`, `client/js/ui-dashboards.js`, `run_client.py`
- Decision (T-003 = a): automatic Ollama→Gemini failover stays; **no manual selector**. This is a read-only indicator only.
- Client status: Badge UI wired in `client/js/ui-dashboards.js` and styled in `client/style.css`.
- Blocked by: T-004 (`POST /api/trainer/checkin` returns 500 error due to JSON parse failure in `llm_client.generate_json`).
- DoD: run the client (port 5000), trigger a check-in, screenshot the badge showing the provider; commit & push.

### [T-008] Ollama server: the model is running on the CPU, not the GPU
- Owner: anders (server-side, 192.168.107.15)
- Status: done (2026-07-23)
- Priority: P1
- Created-by: claude
- **RESOLVED.** Cause: a 595 driver had been installed and then removed back to 580, leaving
  the 595 kernel module loaded while userspace was on 580.173 (`nvidia-smi` → "Driver/library
  version mismatch"). Ollama's NVML call failed and it fell back to the CPU silently. Anders
  rebooted; verified from here: `size_vram 11.09 GB = 100 % GPU`, generation 2.0 → **35.5
  tok/s**, prompt reading 23 → **1084 tok/s**, the 1226-token benchmark 64.45 s → **1.85 s**.
- Follow-up: `num_ctx=12288` fits fully after all (11.09 GB on a 12 GB card), so it does not
  need lowering. Documented in the README with the measured before/after numbers.
- Ready to run on that host: `bash scripts/diagnose-ollama.sh` (read-only; reports driver
  state, GPU device nodes, unit-file overrides and Ollama's own startup decision).
- Ruled out from here: it is **not** a VRAM-fit problem. A 4.9 GB model at `num_ctx=2048`
  with `num_gpu=99` forced still loaded at `size_vram = 0`, so the Ollama process has no
  usable CUDA at all. Server is Linux with the standard install
  (`/usr/share/ollama/.ollama/models`), Ollama 0.32.1.
- Everything tunable from Freja's side is already done (T-009); this is the remaining ~15-20x.
- Measured 2026-07-23 against the live server: `/api/ps` reports `size_vram = 0.00 GB` for
  `qwen2.5:14b` - 0 % GPU offload. Generation runs at **2.0 tok/s** and prompt evaluation at
  **23 tok/s** (a 1226-token prompt takes 53 s to read). On the RTX 3060 the same work should
  be ~30-40 tok/s and ~1000+ tok/s. Everything else about Ollama's speed is a rounding error
  next to this.
- Diagnose on the box: `nvidia-smi`, `ollama ps`,
  `journalctl -u ollama -n 200 | grep -iE "cuda|gpu|library|rocm"`.
- Also note `num_ctx = 12288` puts the model at 11.68 GB against a 12 GB card, so it may not
  fit even once CUDA works. `OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0` roughly
  halves the KV cache; dropping to 8192 is the blunt alternative. The claim in
  `ollama_client.py` that 12288 keeps it at 100 % GPU (~10.8 GB) no longer holds.

### [T-006] Admin portal: verify the AI provider selector against the live backend
- Owner: anders (manual step, no agent work needed)
- Status: todo
- Priority: P2
- Created-by: claude
- Steps: open the backend control center → **PULL FROM GITHUB & RESTART** → confirm the new
  **AI PROVIDER** card turns green for Ollama, pick *Ollama only* / *Gemini only* → **SAVE ALL
  SETTINGS** → the card follows the choice, and stopping Ollama turns its light red.
- Note: the selector governs the trainer, learning and Codex features (everything behind
  `llm_client`). The main chat (`gemini_proxy.py`) and the Telegram bot still call Gemini
  directly; routing those through `llm_client` would be a separate task.

### [T-004] Backend: Fix JSON parsing failure in `/api/trainer/checkin`
- Owner: claude
- Status: todo
- Priority: P1
- Created-by: antigravity
- Files: `backend/llm_client.py`, `backend/routes/trainer/checkin.py`
- Problem: Calling `POST /api/trainer/checkin` fails with HTTP 500: `{"detail":"Unterminated string starting at: line 3 column 23 (char 121)"}` when `llm_client.generate_json()` attempts to parse LLM response JSON.
- DoD: Make `llm_client.generate_json()` handle truncated/unescaped LLM JSON responses cleanly (or repair JSON formatting) so `/api/trainer/checkin` completes successfully with top-level `provider` field.

---

## Done

- **[T-001]** Unify LLM providers behind `llm_client` — DONE (commit `5358ffd`). Ollama-first, Gemini-fallback facade; trainer routes + learning_service + codex_service all route through it. `pytest -k "trainer or gemini or learning or codex"` → 74 passed.
- **[T-009]** Ollama configuration + documentation — DONE (claude). `num_ctx` and `keep_alive`
  are now portal settings (`freja_ollama_num_ctx`, `freja_ollama_keep_alive`) with validated
  fallbacks, so the deployment is matched to its hardware without a code change; new fields in
  the admin portal. Added `scripts/diagnose-ollama.sh` (read-only GPU/driver/unit/startup-log
  diagnostic for the Ollama host) and a full **AI Providers** section in the README: provider
  modes, every settings key, the measured CPU-vs-GPU numbers, how to read `size_vram`, and the
  recommended `systemctl edit ollama` environment for a 12 GB card. `pytest` → 352 passed.
- **[T-007]** Freja's backend self-awareness + Ollama latency work — DONE (claude). New
  `backend/services/system_context.py` builds one authoritative block (provider setting, each
  provider's state and model, both hosts, integrations, allowed tools) used by both the HUD
  chat and the Telegram bot; credentials are reported as configured/not configured only, since
  the block goes to Google verbatim in Gemini mode. The engine that actually serves a reply is
  now stated from inside the provider branch (`build_runtime_provider_line`) instead of guessed
  before dispatch. Fixed in `gemini_proxy.py`: provider health read from the wrong key (both
  engines always reported OFFLINE), the same expression making an Ollama-only setup fail with
  HTTP 400, `freja_ollama_url` vs the real `freja_ollama_base_url`, and a `"llama3"` default that
  contradicted the model actually called. Latency: `keep_alive=30m` (was Ollama's 5 min default,
  costing a measured 10.7 s reload), a `num_predict` ceiling on text replies, and the provider
  probe now shared through `llm_client.get_provider_status()` instead of two live round-trips
  per chat turn. `pytest` → 340 passed, 3 skipped. Remaining latency work is T-008 (GPU).
- **[T-005]** Admin portal: manual AI provider selector + reachability indicator — DONE (claude).
  New setting `freja_llm_provider` (`auto` | `ollama` | `gemini`) read by `llm_client`; `auto`
  keeps the T-003 failover, the pinned modes never silently answer from the other engine.
  `ollama_client.check_health()` / `gemini_client.check_health()` (never raise; the Gemini one
  strips the API key out of failure messages) feed `llm_client.check_providers()` and the new
  `GET /api/system/llm-status` (10 s cache, `?refresh=true` to bypass). Admin portal gained an
  **AI PROVIDER** status card with a green/red light per provider, plus provider/Ollama
  URL/Ollama model controls. `pytest` → 325 passed, 3 skipped. Anders' request supersedes
  T-003's "no manual selector" decision; the automatic mode remains the default.
- **[T-003]** Provider selection decision — DONE: chose **(a) automatic failover** (no manual selector). Backend enablement shipped by Claude: `llm_client.get_active_provider()` (records serving provider on a ContextVar) + `POST /api/trainer/checkin` now returns a `provider` field; new `tests/test_llm_client.py` (4 tests). Client indicator handed to Antigravity as **T-002**.
