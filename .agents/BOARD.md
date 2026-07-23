# Freja — Shared Task Board

Coordination queue for **Claude Code** and **Antigravity**.
Protocol: [`.agents/COLLABORATION.md`](COLLABORATION.md). Each task has exactly one owner.

> **How to use:** at session start, `git pull`, find the highest-priority task where
> `Owner == you` and `Status ∈ {todo, review}`, set it `in-progress`, work it, then set
> `done` or hand off. Commit & push after every change to this file.

Status: `todo · in-progress · review · blocked · done` · Priority: `P1 · P2 · P3`

---

## Active

### [T-001] Unify LLM providers behind `llm_client.py`
- Owner: claude
- Status: review
- Priority: P1
- Created-by: anders
- Files: `backend/services/llm_client.py`, `backend/services/ollama_client.py`, `backend/services/gemini_client.py`, `backend/routes/trainer/generation.py`, `backend/routes/trainer/optimize.py`, `backend/routes/trainer/checkin.py`
- Spec: Route trainer LLM calls (generation, optimize, check-in) through a single `llm_client` facade so the provider is chosen in one place.
- Progress (Claude, 2026-07-23): Facade implemented in the working tree — `generate_text()` / `generate_json()` try Ollama first and fall back to Gemini only when Ollama fails AND a Gemini key is configured. All three trainer routes import and call `llm_client.generate_json`. Acceptance tests GREEN: `pytest -k "trainer or gemini or learning or codex"` → **74 passed, 3 skipped**. Code is verified but still **uncommitted** in the working tree — awaiting Anders' go-ahead to commit (DoD: committed & pushed + `graphify update .`).
- Contract for T-002: `llm_client.generate_text(prompt, system_instruction="", temperature, timeout) -> str` and `generate_json(prompt, schema=None, system_instruction="", temperature, max_tokens, timeout) -> dict`. **Provider selection is automatic (Ollama→Gemini failover); there is currently NO client-facing parameter to pick a provider.**

### [T-002] Client: surface the active LLM provider + verify chat after failover
- Owner: antigravity
- Status: blocked
- Priority: P2
- Created-by: anders
- Depends-on: T-003
- Files: `client/app.js`, `client/gemini.js`, `client/index.html`, `run_client.py`
- Spec: Original ask was a user-facing provider *selector*. Backend finding (T-001 contract): selection is automatic failover with no selection endpoint, so this needs a scope decision (T-003) before UI work. If automatic failover stays, this shrinks to a read-only "active provider" indicator.
- DoD: run the client (port 5000), click through a chat exchange, capture a screenshot showing correct behavior; committed & pushed.
- Handoff-notes (from Claude, 2026-07-23): Do not start until T-003 is resolved — a real selector requires a backend `provider` parameter that does not exist yet. If you scaffold UI early, code against the T-001 contract above and expect only automatic failover for now.
- ▶ Antigravity prompt (activates after T-003; Claude will finalize it then): "Read `.agents/BOARD.md` task T-002 and the T-001 contract. Implement the agreed UI (active-provider indicator, or selector if T-003 chose a manual one), run the client on port 5000, send a test chat message, screenshot the result, then commit & push. If the backend response lacks the field you need, stop and add a task for `claude` on the board."

### [T-003] Decide: manual provider selection vs. automatic failover
- Owner: anders
- Status: todo
- Priority: P2
- Created-by: claude
- Depends-on: T-001
- Spec: The T-001 facade does automatic Ollama→Gemini failover with no user-facing selector; T-002 assumed a selector. Decision needed — (a) keep automatic failover → T-002 becomes a passive "active provider" indicator (needs the API to return which provider answered); or (b) want a manual selector → Claude adds a `provider` parameter to the trainer endpoints and `llm_client`, then T-002's selector is buildable.
- Handoff-notes (from Claude, 2026-07-23): Blocking T-002. If Anders picks (b), the backend part comes back to `claude` as a new task.

---

## Done

_(move completed tasks here with a one-line result)_
