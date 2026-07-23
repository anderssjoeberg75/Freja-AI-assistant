# Freja — Multi-Agent Collaboration Protocol

Two autonomous coding agents develop Freja together:

- **Claude Code** (Anthropic, Opus 4.8) — entry file: root `CLAUDE.md`
- **Antigravity** (Google, Gemini 3, agent-first IDE) — entry file: `.agents/AGENTS.md`

They cannot call each other directly. They coordinate **through this repository**: a
shared task board (`.agents/BOARD.md`) synchronized over git. The rule is simple —
**whichever agent is best suited to an operation performs it, and hands the next step
to the other by writing a task on the board.**

This file is the single source of truth for *who does what* and *how work is handed
off*. `CLAUDE.md` and `.agents/AGENTS.md` both point here — do not duplicate rules,
update them here.

---

## 1. Roles — who is best at what

### Claude Code owns (backend & correctness)
- **Backend Python**: `backend/**` — services, routes, middleware, `database.py`, `config.py`, `server.py`.
- **LLM provider layer**: `llm_client.py`, `gemini_client.py`, `ollama_client.py`, `codex_service.py` — architecture, unification, correctness.
- **Data layer**: schema, `alembic` / `migrations/**`, encryption, keys/secrets handling.
- **Auth / CORS / origins / security** — and security review of any change.
- **Tests**: `tests/**` — authoring, fixing, reasoning. Runs `pytest`; **never starts `server.py`** (backend is remote-hosted).
- **Correctness & edge-case review** of any diff before it merges.
- **Knowledge graph**: codebase Q&A via `graphify query|explain|path` and `graphify update .` maintenance after code changes.

### Antigravity owns (frontend & verification)
- **Client / frontend**: `client/**` (`app.js`, `camera.js`, `gemini.js`, `speech.js`, `visualizer.js`, `theme.js`, `style.css`, `index.html`, …) and `run_client.py`.
- **Live verification in a browser** — its native strength. Runs the client (port 5000), clicks through flows, captures screenshots / recordings / walkthroughs (Artifacts) as **proof** that a change works.
- **Integration / E2E checks** against the running backend.
- **Multimodal work**: screenshots, image/audio/camera features, the `.docx` PT-coach guide, visual/responsive/dark-mode/accessibility QA.
- **Fast scaffolding** and boilerplate generation across many files.

### Shared (either agent)
- **Docs**: `docs/**`, `README.md` — the agent that changes behavior updates the docs for it.
- Small cross-cutting fixes are done by whoever is already in that file.

### Non-negotiable rules for BOTH (from `.agents/AGENTS.md`)
- All code, comments, and docs in **English**. Freja's *user-facing* responses are in **Swedish**.
- Add descriptive **logging / comments** for traceability.
- **Never** start the backend (`server.py`) locally — it runs on its dedicated server (192.168.107.15).
- After completing a unit of work: **`git add` → `commit` → `push`**. Git is the coordination medium; unpushed work is invisible to the other agent.

---

## 2. The board — `.agents/BOARD.md`

The board is the shared queue. Every task is owned by exactly one agent at a time.

**Task template:**
```
### [T-000] Short title
- Owner: claude | antigravity
- Status: todo | in-progress | review | blocked | done
- Priority: P1 | P2 | P3
- Created-by: claude | antigravity | anders
- Files: paths likely involved
- Depends-on: T-xxx (optional)
- Spec: what "done" means, incl. API contract / expected UI
- Handoff-notes: context the receiving agent needs
```

**Status lifecycle:** `todo → in-progress → review → done` (`blocked` when waiting on a dependency).

---

## 3. Session start — every agent, every time

1. `git pull` (or confirm the working copy is current).
2. Read `.agents/BOARD.md`. Filter to `Owner == you` and `Status ∈ {todo, review}`.
3. Pick the highest-priority such task. Set it `in-progress`, commit the board change, and start.
4. If nothing is assigned to you, either continue your role's backlog or check for `blocked` tasks whose dependency is now `done`.

---

## 4. Handing off — "feed the other agent"

When you finish a step whose *next* step belongs to the other agent, **create or reassign
a board task for them** instead of doing it yourself. Fill in `Handoff-notes` with
everything they need (contract, repro, file paths, screenshots).

**Handoff matrix:**

| You just did… | Hand off to… | New task |
|---|---|---|
| Claude changed a backend endpoint / contract | Antigravity | Wire the client to it + browser-verify. Include the endpoint schema. |
| Antigravity needs a new/changed endpoint, or hit a backend bug during UI work | Claude | Implement/fix it. Include repro + desired contract. |
| Claude made a change needing visual / E2E proof | Antigravity | Run the client, verify the flow, attach screenshots. |
| Antigravity touched a security-sensitive flow (tokens, secrets in client) | Claude | Security & correctness review. |
| Either produced a diff to be reviewed | the other | Review (Claude: backend/security correctness · Antigravity: UX/visual regressions). |

**Mechanics of a handoff:**
1. Set the task's `Owner` to the other agent and `Status` to `todo` (or `review`).
2. Write `Handoff-notes`.
3. `commit` (message: `board: hand off T-0xx to <agent> — <why>`) and `push`.

The other agent picks it up at its next session start (§3). This is how tasks flow
back and forth without the two tools ever talking directly.

---

## 5. Avoiding collisions

Both tools may run against the same working copy.

- **Prefer turn-based**: run one agent at a time on `main`. Simplest and safest for a solo maintainer.
- **If running in parallel**, use one branch per task (`claude/T-0xx`, `antigravity/T-0xx`); keep `BOARD.md` edits to your own task block so merges stay trivial (append-only where possible).
- Only ever edit a task **you own**. Never silently take a task owned by the other agent — reassign it on the board with a note first.
- Always `pull` before you start and `push` the moment a unit is done.

---

## 6. Definition of Done (per task)

- Code & comments in English; logging added.
- Backend change → relevant `pytest` passing (Claude); frontend change → **browser-verified with a screenshot** (Antigravity).
- Docs updated if behavior changed.
- `graphify update .` run if code structure changed.
- Committed **and pushed**.
- Board updated: `Status: done`, or reassigned as a handoff.

---

## 7. Human orchestrator

Anders can add tasks to the board (`Created-by: anders`) with an explicit `Owner`, or
leave `Owner` blank to let the agents route it by §1. Anders has the final say on the
division of labor — this file is the adjustable rulebook, not a fixed contract.
