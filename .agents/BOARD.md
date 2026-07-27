# Freja — Shared Task Board

Coordination queue for **Claude Code** and **Antigravity**.
Protocol: [`.agents/COLLABORATION.md`](COLLABORATION.md). Each task has exactly one owner.

> **How to use:** at session start, `git pull`, find the highest-priority task where
> `Owner == you` and `Status ∈ {todo, review}`, set it `in-progress`, work it, then set
> `done` or hand off. Commit & push after every change to this file.

Status: `todo · in-progress · review · blocked · done` · Priority: `P1 · P2 · P3`

---

## Active

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

---

## Polar / Coros integrations — researched 2026-07-25

Anders asked what it takes to connect Polar and Coros, matching the existing Garmin/Strava/
Withings pattern (`backend/routes/{garmin,strava,withings}.py`, all OAuth2 authorization-code
flows storing a refresh token via `set_api_key()`/`get_api_key()`, a `/api/<provider>/callback`
route, and a `run_<provider>_sync_task` background sync). Per `.agents/COLLABORATION.md`, new
backend Python integrations are Claude's lane — Antigravity only picks up the follow-on
UI/settings piece once a backend is live, same as T-024..T-031 did for Garmin. Both turned out
buildable without waiting on anyone: Polar via its official self-serve OAuth2 API, Coros via a
username/password unofficial API mirroring the Garmin pattern.
**Split for parallel building (2026-07-25):** Claude takes T-032 (Polar), Antigravity takes
T-033 (Coros) — a deliberate one-off exception to the usual backend-is-Claude's-lane rule, so
both land at the same time instead of queued.
**2026-07-27 decision: on hold.** Both need a real external account before they can be built
end-to-end (Polar developer-registration client_id/secret; Coros username/password) — Anders
chose to leave them as `todo` rather than have an agent build untestable code against them.
Pick either back up once those credentials exist.

### [T-032] Backend: Polar AccessLink integration (activity, sleep, recovery)
- Owner: claude
- Status: todo
- Priority: P3
- Created-by: anders
- Files (expected, mirroring `withings.py`): `backend/routes/polar.py` (new), `backend/models.py`,
  `backend/database.py`, `backend/routes/settings.py`, `backend/routes/trainer/shared.py` (if
  folded into `unified_sessions()`/training-load prompts like Garmin/Strava), `tests/test_polar_routes.py` (new)
- **Registration (self-serve, no approval wait):** create/use a Polar Flow account, register as
  a third-party developer at admin.polaraccesslink.com, "Create client" → register the redirect
  URI (`https://<backend-host>/api/polar/callback`) → save the issued **client_id**/**client_secret**
  as `freja_polar_client_id`/`freja_polar_client_secret` via `set_api_key()`.
- **OAuth2 flow** (same shape as `withings.py`'s callback): authorize at
  `auth.polar.com/oauth/authorize`, token at `POST auth.polar.com/oauth/token` — access token
  valid 12h, refresh token stored as `freja_polar_refresh_token`. Scopes needed:
  `activity:read`, `sleep:read`, `training_sessions:read`, `continuous_samples:read`, `profile:read`.
- **Important divergence — verify before implementing:** AccessLink may **not** be a plain
  "GET data since date" API like Withings; it's unconfirmed whether a one-time `POST /v3/users`
  registration step is required post-OAuth, or whether historical data needs a
  transaction-create/list/commit sequence. Pull the current official v3/v4 API reference before
  writing `polar.py` — do not assume the Withings-style single-GET shape carries over.
- **Data available:** daily activity summaries, training sessions, sleep (phases/score), Nightly
  Recharge (HRV, breathing rate), continuous HR, fitness tests (VO2 max), sports profiles.
- **Rate limits:** 3,000 req/15 min, 100,000 req/24h per client_id — a 429 needs the same
  back-off classification pattern as Garmin's `_classify_garmin_error()` (see Done log, old T-015).
- Suggested first pass: mirror Withings exactly (OAuth callback + refresh-token storage +
  periodic activity/sleep/recovery pull into new tables) rather than every endpoint at once.
- **Ruled out:** no viable unofficial/username-password route for Polar exists (the one
  candidate, `campbellr/flow-client`, is archived since 2021 and has no sleep/activity data) —
  official AccessLink OAuth2 is the only path, which is fine since it's already self-serve.
- Handoff (once live): a Polar settings-panel card (client id/secret fields + "Connect Polar"
  button, same PT-panel location as the Garmin/Strava/Withings cards) is Antigravity's follow-up.

### [T-033] Backend: Coros integration
- Owner: antigravity
- Status: todo
- Priority: P3
- Created-by: anders
- **Deliberate exception to the usual lane split:** backend Python, normally Claude's lane —
  split so Polar (T-032, Claude) and Coros (this, Antigravity) build in parallel. Claude still
  owns correctness/security review of whatever lands here.
- **Two unofficial-route options found, neither needs Coros's approval** (the branded
  partner-program application route is slow/case-by-case and not recommended):
  1. **Official Coros MCP server** (`mcp.coros.com/mcp`) — OAuth browser popup, no dev
     application, but Freja's backend has **no MCP-client capability at all**
     (`backend/services/tool_registry/` is a bespoke system, unrelated to MCP) — building one
     just for this is a meaningfully bigger lift than one more OAuth route.
  2. **Unofficial Coros Training Hub API** (reverse-engineered, two open-source references:
     `github.com/cygnusb/coros-mcp`, `github.com/CuberL/coros-mcp`) — plain username/password
     login against Training Hub, same shape as `garminconnect`'s pattern already used in
     `garmin.py`. **Recommended path** — fits the existing integration shape with no new
     architecture. Note: logging in via the mobile-API path logs the user's phone out of the
     Coros app each time (real UX cost, same category as Garmin's token churn).
- **Build plan (path 2), mirroring `backend/routes/garmin.py`:** `backend/routes/coros.py` (new),
  username/password stored via `set_api_key()` (`freja_coros_email`/`freja_coros_password`),
  a background sync task, new models (`coros_activities`, `coros_health` — copy
  `GarminActivity`/`garmin_health`'s column patterns), settings wiring consistent with
  `backend/routes/settings.py`.
  - **Login** — `POST https://teameuapi.coros.com/account/login` (region-specific host; `eu`
    for a Swedish account). Body `{account, accountType, pwd}`, `pwd` = MD5 hash of the
    password. Response: `data.accessToken`, `data.userId`. Token ~24h; re-login silently on
    expiry (same pattern as Garmin's T-015 reauth). Header `accessToken: <token>` +
    `yfheader: {"userId": <userId>}` (JSON-encoded) on every later request.
  - **Activity list** — `GET /activity/query?startDay=&endDay=&pageNumber=&size=` →
    `data.dataList`, each with `labelId` (activity id), `sportType`, `startTime`/`endTime`,
    `totalTime`, `distance`, `avgHr`/`maxHr`, `trainingLoad`, `avgPower`, ascent/descent.
  - **Activity detail** — `POST /activity/detail/query` (form: `labelId`, `userId`,
    `sportType`) → strip `graphList`/`frequencyList`/`gpsLightDuration` before storing (raw
    sample streams nothing here consumes yet).
  - **Sleep** — different host: `POST https://apieu.coros.com/coros/data/statistic/daily`,
    token needed **both** as query param `accessToken` and header `accesstoken` (lowercase,
    different from the other endpoints). `result: "1019"` means expired token — re-login and
    retry once, don't fail the sync.
  - **HRV** — `GET /dashboard/query` → `data.summaryInfo.sleepHrvData.sleepHrvList[]`.
  - **Resting HR / training load** — `GET /analyse/dayDetail/query?startDay=&endDay=` (up to
    ~24 weeks/call) → `data.dayList[]` (`rhr`, `trainingLoad`, `trainingLoadRatio`, `ati`, `cti`).
  - **VO2max / threshold** — `GET /analyse/query` → `data.t7dayList[]` (`vo2max`, `lthr`, `ltsp`).
  - Every endpoint returns a top-level `result` code (`"0000"` = success) — preserve the raw
    code on error rather than swallowing it into a generic failure, same reasoning as Garmin's
    `_classify_garmin_error()`.
  - **Field-name caveat:** none of this is officially documented — cross-check
    `cygnusb/coros-mcp`'s and `CuberL/coros-mcp`'s source before trusting a field name above,
    and degrade to `None`/skip on an unexpected shape rather than crash the sync (same defensive
    pattern `garmin.py` uses for Garmin's own undocumented endpoints).
- Add a settings card (email/password + connect/sync-status) at the same PT-panel location as
  the other provider cards — this piece is Antigravity's normal lane either way.
- Write tests mirroring `tests/test_garmin_routes.py`'s shape (fake client, successful sync
  stores rows, bad login classified not just failed, malformed response degrades gracefully).

---

## Done

- **[T-034]** Backend & UI: Fitbit Web API integration (activities, sleep, heart rate, recovery) — DONE (antigravity). Built `backend/routes/fitbit.py`, `fitbit_health` model & table, OAuth2 authorization-code callback (`/api/fitbit/callback`), daily REST API sync (`/api/fitbit/sync`), PT health baselines & trends integration in `backend/routes/trainer/shared.py`, client settings modal & OAuth link in `client/index.html`, `ui-init.js`, `ui-events.js`, and comprehensive unit test suite in `tests/test_fitbit_routes.py` (6 tests). All 435 tests passing.
- **[T-004]** Fix JSON parsing failure in `/api/trainer/checkin` — DONE (2026-07-27, claude).
  Root cause: `ollama_client.generate_json`/`gemini_client.generate_json` did a naive
  `json.loads()` with no handling for output truncated by hitting the token cap
  (`num_predict`/`maxOutputTokens`) mid-string — exactly the "Unterminated string" crash.
  New `backend/services/json_repair.py` (`parse_llm_json`): closes whatever string/object/array
  was left open by truncation and re-parses once, raising the original error if that doesn't
  produce valid JSON (so a genuinely malformed response still surfaces honestly). Wired into
  both providers at their single shared choke point, so every `generate_json` caller benefits,
  not just check-in. Also bumped checkin's `max_tokens` 1500→2500 for headroom. 10 new tests
  (`tests/test_json_repair.py`); full suite `pytest` → 426 passed, 3 skipped. Verified live:
  ran a real check-in against the remote backend end-to-end, got a complete briefing back with
  no 500.
- **[T-002]** Client: show which LLM provider answered the daily check-in — DONE (2026-07-27,
  claude). Was blocked on T-004 only; the badge itself was already wired
  (`client/js/ui-dashboards.js:751-758`, styled in `client/style.css`). Browser-verified against
  the running client (port 5000) proxying to the remote backend: triggered a live check-in,
  confirmed the badge renders `Svar från: Ollama` with no error.
- **Garmin/Strava integration batch — imported from GitHub issues #176–#189 (2026-07-24),
  all DONE (claude) unless noted.** Backend-Python work; a handful of issues also had a client
  UI deliverable split off as its own Antigravity task (T-024..T-031, all done — provider cards,
  HUD readiness/zone charts, PT-panel lap table/benchmarks/adherence-warning/source-badge, the
  "push to watch" button). Condensed summary per backend piece:
  - **T-011** Multi-activity-per-day Garmin sync bug fixed (`GarminActivity`/`garmin_activities`
    table, per-day loop no longer `break`s after the first match).
  - **T-012** Garmin sync request volume cut via date-ranged `get_body_battery`/`get_daily_steps`
    (verified against the installed client's own docstrings that `get_weekly_stress`/
    `get_weekly_intensity_minutes` are weekly aggregates, not daily — kept those per-day).
  - **T-013** CTL/ATL/TSB/ACWR captured from Garmin's own training-load DTOs; TSB always
    computed on read, never stored.
  - **T-014** Training Readiness score/level/feedback now stored every day (was previously only
    scavenged as a fallback, never persisted); leads the daily check-in.
  - **T-015** Garmin auth errors classified (`auth_required`/`rate_limited`/generic) +
    `POST /api/garmin/reauth` + token-age staleness warning. **MFA deliberately not built** —
    needs confirming 2FA is even enabled on the account plus a stateful two-call design; a
    2FA account's reauth just fails classified as `auth_required` for now.
  - **T-016** Per-activity Garmin detail fetch (`get_activity()`), capped at 10/call, own
    `detail_fetched_at` marker per activity for safe retry.
  - **T-017** Garmin strength sets auto-imported into the PT strength log
    (`backend/services/garmin_exercises.py` Swedish↔Garmin exercise-name table); manual entries
    untouched, re-import only replaces that activity's own Garmin-sourced rows.
  - **T-018** Time-in-HR-zone capture per session (`garmin_activity_zones`); weekly easy/hard
    split surfaces in the plan prompt only when `easy_pct < 80%` (Tier B deviation gate).
  - **T-019** Lap splits captured and exposed as a tool (`get_garmin_activity_laps`), **not**
    resident in any prompt (Tier C). **Step 3 (grading execution against plan prescription)
    deliberately deferred** — real NLP/heuristic design work; starting point is
    `compute_adherence()` in `shared.py` + `trainer_plans.advice_text`'s structured JSON +
    `garmin_activity_laps.intensity_type`.
  - **T-020** Performance benchmarks (threshold pace/HR, endurance score, fitness age, race
    predictions, PRs) pulled weekly, self-limited via a stored timestamp. **`get_activity_types()`
    deferred** (marginal payoff, explicitly lowest-priority in the source issue).
  - **T-021** Adherence no longer silently reports 0% when a sync is broken — unions
    Garmin+Strava completions, only `None`s out with `reliable: False` when *both* sources are
    down for the window.
  - **T-022** `unified_sessions()`: Garmin-first merge with Strava filling gaps, matched on
    start-time±10min **and** duration±10% (not date alone). Migrated 4 of 5 call sites onto it;
    `compute_adherence()` deliberately left on its own (already correct, didn't need the
    duplicate-instance matching this helper adds).
  - **T-023** Prompt-budget tiering decision, made retroactively to consolidate the inline calls
    above: Tier A (always resident) = readiness, CTL/ATL/ACWR, load-balance, today's planned
    session, threshold benchmarks. Tier B (resident only when it deviates) = the HR-zone split.
    Tier C (tool-call only) = laps, per-session detail history, race predictions/PRs. Budget
    guardrails added: `CHAT_CONTEXT_TOKEN_BUDGET = 800`, `PLAN_PROMPT_LOAD_SECTION_TOKEN_BUDGET
    = 2000` in `shared.py`.
  - **T-010** Push planned workouts to the Garmin watch —
    `backend/services/garmin_workout.py` builds a single time-based MAIN-step workout;
    `garmin_pushed_workouts` tracks what's on the watch per `(plan_id, date)` so a re-push
    updates rather than duplicates. `POST`/`DELETE /api/garmin/workouts/push`. **Steps 2-3
    deferred** (richer HR/pace-zone step structure; strength sessions at exercise level) — both
    real follow-ups with their groundwork (benchmarks, the exercise-name table) already in
    place.
- **[T-008]** Ollama server running on CPU instead of GPU — RESOLVED (2026-07-23, anders).
  Cause: a driver up/downgrade left the 595 kernel module loaded against 580 userspace
  (`nvidia-smi` → "Driver/library version mismatch"), so Ollama's NVML call silently failed and
  fell back to CPU. A reboot fixed it: generation 2.0 → 35.5 tok/s, prompt eval 23 → 1084 tok/s.
  `num_ctx=12288` fits fine at 11.09GB/12GB, no lowering needed. Diagnostic script:
  `scripts/diagnose-ollama.sh` (read-only GPU/driver/unit/startup-log check).
- **[T-001]** Unify LLM providers behind `llm_client` — DONE (commit `5358ffd`). Ollama-first, Gemini-fallback facade; trainer routes + learning_service + codex_service all route through it. `pytest -k "trainer or gemini or learning or codex"` → 74 passed.
- **[T-009]** Ollama configuration + documentation — DONE (claude). `num_ctx` and `keep_alive`
  are now portal settings (`freja_ollama_num_ctx`, `freja_ollama_keep_alive`) with validated
  fallbacks, so the deployment is matched to its hardware without a code change; new fields in
  the admin portal. Added `scripts/diagnose-ollama.sh` and a full **AI Providers** section in
  the README.
- **[T-007]** Freja's backend self-awareness + Ollama latency work — DONE (claude). New
  `backend/services/system_context.py` builds one authoritative block (provider setting, each
  provider's state and model, both hosts, integrations, allowed tools) used by both the HUD
  chat and the Telegram bot. Fixed in `gemini_proxy.py`: provider health read from the wrong
  key, an Ollama-only setup failing with HTTP 400, `freja_ollama_url` vs the real
  `freja_ollama_base_url`, and a `"llama3"` default that contradicted the model actually called.
  Latency: `keep_alive=30m`, a `num_predict` ceiling, shared provider-status probing.
- **[T-005]** Admin portal: manual AI provider selector + reachability indicator — DONE (claude).
  New setting `freja_llm_provider` (`auto` | `ollama` | `gemini`) read by `llm_client`; `auto`
  keeps the T-003 failover, the pinned modes never silently answer from the other engine.
  `GET /api/system/llm-status` (10s cache) feeds a new **AI PROVIDER** status card
  (green/red light per provider) in the admin portal.
- **[T-003]** Provider selection decision — DONE: chose **(a) automatic failover** (no manual selector). Backend enablement shipped by Claude: `llm_client.get_active_provider()` (records serving provider on a ContextVar) + `POST /api/trainer/checkin` now returns a `provider` field; new `tests/test_llm_client.py` (4 tests). Client indicator handed to Antigravity as T-002.
