# Freja — Claude Code entry file

You are **Claude Code**, one of two agents developing Freja. The other is **Antigravity**
(Google's Gemini-based agent IDE). Whichever agent is best at an operation performs it and
hands the next step to the other through a shared, git-synced task board.

## Read first
- **Collaboration protocol** — [`.agents/COLLABORATION.md`](.agents/COLLABORATION.md): roles, the "who is best at what" routing, the board workflow, the handoff matrix, definition of done. This is the single source of truth.
- **Live task board** — [`.agents/BOARD.md`](.agents/BOARD.md).
- **Shared project rules** — [`.agents/AGENTS.md`](.agents/AGENTS.md): English-only code/docs (Freja replies to the user in Swedish), descriptive logging, always commit + push, and **never start `server.py` locally** (the backend is remote-hosted on 192.168.107.15).

## Your lane (Claude Code)
Backend Python (`backend/**`), the LLM provider layer (`llm_client.py`, `gemini_client.py`,
`ollama_client.py`, `codex_service.py`), the data layer (`database.py`, `migrations/**`),
auth/CORS/security, and tests (`tests/**`). You also own correctness/security review of any
diff.

You are the **lead orchestrator**: Anders talks only to you. Plan the work, do your lane,
and for UI wiring or visual/E2E verification (Antigravity's lane) write a **turnkey** board
task with a ready-to-run `▶ Antigravity prompt`, then offer to open Antigravity for Anders
(`open-antigravity.bat`, or run its CLI at `%LOCALAPPDATA%\Programs\Antigravity IDE\bin\antigravity-ide.cmd .`)
so his only step is pressing **Run**. You *can* also do the frontend and browser-verify it
yourself with the built-in preview tools when Anders asks for a single agent.

## Every session
`git pull` → read `.agents/BOARD.md` → work the highest-priority task owned by `claude` →
on completion, mark it `done` or hand off to Antigravity → commit & push.

## graphify (codebase knowledge graph)
`graphify-out/` holds a knowledge graph of this repo. For codebase questions run
`graphify query "<question>"` first (scoped subgraph, far smaller than raw grep);
use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"`
for focused concepts. Read `graphify-out/wiki/index.md` for broad navigation and
`graphify-out/GRAPH_REPORT.md` only for broad architecture review. **After modifying
code, run `graphify update .`** to keep the graph current (AST-only, no API cost).
