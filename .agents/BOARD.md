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
