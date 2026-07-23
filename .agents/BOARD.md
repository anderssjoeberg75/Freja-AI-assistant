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
- Status: in-progress
- Priority: P1
- Created-by: anders
- Files: `backend/services/llm_client.py`, `backend/services/ollama_client.py`, `backend/services/gemini_client.py`, `backend/services/codex_service.py`, `backend/routes/trainer/generation.py`, `backend/routes/trainer/optimize.py`, `backend/routes/trainer/checkin.py`
- Spec: Route trainer LLM calls (generation, optimize, check-in) through a single `llm_client` abstraction so provider (Gemini / Ollama / Codex) is swappable. Keep behavior identical; add logging at each provider boundary.
- DoD: `pytest tests/ -k "trainer or gemini or learning or codex"` passing; `graphify update .` run; committed & pushed.
- Handoff-notes: When the provider contract is stable, hand off T-002 to Antigravity with the final response schema so the client can expose a provider selector and be browser-verified.

### [T-002] Client: provider selector + verify chat after provider switch
- Owner: antigravity
- Status: blocked
- Priority: P2
- Created-by: anders
- Depends-on: T-001
- Files: `client/app.js`, `client/gemini.js`, `client/index.html`, `run_client.py`
- Spec: Once T-001 lands, surface the selectable LLM provider in the client and confirm the chat flow still works end-to-end when the provider changes.
- DoD: run the client (port 5000), click through a chat exchange for each provider, capture screenshots as proof; committed & pushed.
- Handoff-notes: Blocked until Claude marks T-001 `done` and posts the final response schema here. If a backend gap surfaces during wiring, open a new task owned by `claude`.

---

## Done

_(move completed tasks here with a one-line result)_
