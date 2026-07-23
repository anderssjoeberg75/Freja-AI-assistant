# Project Rules & Guidelines

## Core rules (all agents)

- **GitHub Synchronization**: Always commit and push changes (`git add .`, `git commit`, `git push`) to GitHub after completing coding modifications or bug fixes. Git is how the agents coordinate — unpushed work is invisible to the other agent.
- **Language Requirements**: All text, code, documentation, and comments within the project must be in English. However, Freja (the assistant application) must respond to the user in Swedish.
- **Logging & Traceability**: Always include descriptive logging, notifications, or comments in the code to facilitate troubleshooting and debugging.
- **Backend Execution Restriction**: Never start the backend server (`server.py`) locally or in background processes unless explicitly requested by the user. The backend is hosted and runs on its dedicated server environment (192.168.107.15).

## Multi-agent collaboration — READ THIS

Freja is developed by **two agents working together**: **Antigravity** (you, if you are
reading this as the Antigravity/Gemini agent) and **Claude Code** (Anthropic). Whichever
agent is best suited to an operation performs it and hands the next step to the other
through a shared task board.

- **The protocol is defined in [`.agents/COLLABORATION.md`](COLLABORATION.md)** — roles, the "who is best at what" routing, the board workflow, the handoff matrix, and the definition of done. Read it.
- **The live task board is [`.agents/BOARD.md`](BOARD.md)**.

**Your lane (Antigravity):** the client/frontend (`client/**`, `run_client.py`),
browser-based verification (run the client on port 5000, click through flows, capture
screenshots/walkthroughs as proof), integration/E2E checks, multimodal and UX work, and
fast scaffolding. Backend Python, the LLM provider layer, data/security, and tests are
**Claude Code's lane** — when your work needs any of those, write a task for Claude on the
board instead of doing it yourself.

**Claude Code leads.** Anders drives everything through Claude; your board tasks usually
arrive ready to run with an embedded `▶ Antigravity prompt`. Execute that prompt as-is,
browser-verify, then commit & push. If you hit a backend gap while wiring, add a task for
`claude` on the board rather than touching backend code yourself.

**Every session:** `git pull` → read `.agents/BOARD.md` → work the highest-priority task
owned by you → on completion, mark it `done` or hand off to Claude → commit & push.
