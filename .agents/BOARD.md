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
- Status: todo
- Priority: P2
- Created-by: anders
- Files: `client/app.js`, `client/index.html`, `client/style.css`, `run_client.py`
- Decision (T-003 = a): automatic Ollama→Gemini failover stays; **no manual selector**. This is a read-only indicator only.
- Backend contract (READY): `POST /api/trainer/checkin` now returns a top-level `"provider"` field = `"ollama"`, `"gemini"`, or `"unknown"` — which model actually produced the briefing. Live as of the T-003 backend commit.
- Spec: In the check-in / briefing view, render a small read-only badge with the returned `provider` (e.g. "Svar från: Ollama" / "Svar från: Gemini"). No selector, no extra backend calls — just read the field that is already in the response.
- DoD: run the client (port 5000), trigger a check-in, screenshot the badge showing the provider; commit & push.
- ▶ Antigravity prompt: "Read `.agents/BOARD.md` task T-002. The backend `POST /api/trainer/checkin` response now has a top-level `provider` field (`ollama` | `gemini` | `unknown`). In the client (`client/app.js` plus the check-in/briefing view in `client/index.html`, styled in `client/style.css`), render a small read-only badge showing which model answered, e.g. 'Svar från: Ollama'. Do NOT add a selector. Run the client on port 5000, trigger a daily check-in, screenshot the badge, then commit & push. If the response has no `provider` field where you wire it, stop and add a task for `claude` on the board."

---

## Done

- **[T-001]** Unify LLM providers behind `llm_client` — DONE (commit `5358ffd`). Ollama-first, Gemini-fallback facade; trainer routes + learning_service + codex_service all route through it. `pytest -k "trainer or gemini or learning or codex"` → 74 passed.
- **[T-003]** Provider selection decision — DONE: chose **(a) automatic failover** (no manual selector). Backend enablement shipped by Claude: `llm_client.get_active_provider()` (records serving provider on a ContextVar) + `POST /api/trainer/checkin` now returns a `provider` field; new `tests/test_llm_client.py` (4 tests). Client indicator handed to Antigravity as **T-002**.
