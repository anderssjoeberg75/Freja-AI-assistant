# Freja — Shared Task Board

Coordination queue for **Claude Code** and **Antigravity**.
Protocol: [`.agents/COLLABORATION.md`](COLLABORATION.md). Each task has exactly one owner.

> **How to use:** at session start, `git pull`, find the highest-priority task where
> `Owner == you` and `Status ∈ {todo, review}`, set it `in-progress`, work it, then set
> `done` or hand off. Commit & push after every change to this file.

Status: `todo · in-progress · review · blocked · done` · Priority: `P1 · P2 · P3`

---

## Active

### [T-045] Fix: BigInteger primary keys silently break SQLite autoincrement
- Owner: claude
- Status: done (stale duplicate — actually resolved via `BigIntPK()` in `backend/models.py`,
  see the Done-log entry below; full local `pytest` is green: 488 passed, 3 skipped, 0
  SQLite failures as of 2026-08-06. This Active-section entry was never removed after the fix
  landed — leaving it here as a visible marker rather than deleting history.)
- Priority: P1
- Created-by: claude (found 2026-08-05 while fixing the Postgres cutover crash)
- Files: `backend/models.py`, every table with `Column(BigInteger, primary_key=True,
  autoincrement=True)`, `tests/**`
- Spec: discovered while restoring the production backend after the Postgres cutover
  (see Done log entries below, same session) — running the **local** `pytest` suite (which
  runs against SQLite) now gives **73 failing tests**, all `sqlite3.IntegrityError: NOT
  NULL constraint failed: <table>.id`. Root cause: SQLite only auto-populates a primary key
  on insert when the column's declared type is the literal keyword `INTEGER` (its special
  rowid-alias behavior) — `BigInteger` renders as `BIGINT` in SQLite's dialect, which does
  *not* get that treatment, so any insert that omits `id` (relying on autoincrement) now
  fails. This was introduced by the recent `BigInteger` column-type migration (commit
  `9a3ad75`, done for the Postgres cutover) and wasn't caught because nothing ran the full
  suite against SQLite afterward. Confirmed via `git stash` that this pre-dates today's
  session's own changes. Does **not** affect the live Postgres deployment (Postgres's
  `BigInteger`+`autoincrement=True` correctly becomes a sequence/identity there) — only
  SQLite (local dev, tests, and any SQLite-fallback deployment per `database.py`'s
  connect-failure fallback). Fix options to weigh: revert to `Integer` for SQLite
  specifically via a dialect-conditional column type, or keep `BigInteger` everywhere and
  have `init_db()` do a dialect-specific fixup for SQLite. Full `pytest` must return to 0
  failures against SQLite when done.

### [T-006] Admin portal: verify the AI provider selector against the live backend
- Owner: anders (manual step, no agent work needed)
- Status: todo
- Priority: P2
- Created-by: claude
- Steps: open the backend control center → **PULL FROM GITHUB & RESTART** → confirm the new
  **AI PROVIDER** card turns green for Ollama, pick *Ollama only* / *Gemini only* → **SAVE ALL
  SETTINGS** → the card follows the choice, and stopping Ollama turns its light red.
- Note: the selector governs the trainer, learning and Codex features (everything behind
  `llm_client`), the main HUD chat (`gemini_proxy.py`, already on `_dispatch`), and — as of
  2026-07-27 (T-035) — the Telegram bot too (`telegram_service.py`). The whole user-facing
  surface now follows `freja_llm_provider`.

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

## Multi-tenant backend — analysis 2026-08-05

Anders wants several separate people (not just multiple devices for himself) to use Freja,
each with their own isolated data — own Garmin/Strava/Withings/Fitbit connection, own
training plan, own chat history, own calendar. Analyzed the current backend end-to-end
(`backend/middleware/auth.py`, `backend/routes/auth.py`, `backend/models.py`,
`backend/database.py`, all 20 route files) to scope the work. Findings:

- **A JWT user system already exists but is barely used.** `backend/routes/auth.py` has
  working `register`/`login`/`me` + a `User` table (`backend/models.py:6`). Only
  `chat.py` actually depends on `get_current_user`/`get_current_user_from_token` — every
  other route file (garmin, strava, withings, fitbit, rouvy, google_calendar, trainer/*,
  settings, tools, learning, telegram, instagram, mem0, elevenlabs, gemini, search, sync,
  llm — 18 files) runs unauthenticated-by-identity against one global dataset.
- **The data model is single-tenant, not multi-tenant.** Of ~20 tables in `models.py`, only
  `ApiKey` and `ChatHistory` even have a `user_id` column, and `ApiKey.user_id` is dead
  weight today — `get_api_key()`/`set_api_key()` (`database.py:116`) query by `key_name`
  alone, so every credential (Garmin email/password, Strava/Withings/Fitbit/Google OAuth
  tokens, Gemini key, the Telegram bot token, the shared access token itself) is one global
  value, period. `TrainerProfile` even enforces `CheckConstraint('id = 1')` — the schema
  physically forbids a second user's profile.
- **A real security hole, independent of the multi-tenant decision:**
  `FrejaAuthMiddleware.dispatch` (`backend/middleware/auth.py:159-162`) lets through *any*
  `Authorization: Bearer <anything>` header without validating it — the JWT signature is
  only actually checked inside `chat.py`'s own `Depends(get_current_user)`. Any other route
  is reachable by sending a garbage Bearer token instead of the real `X-Freja-Token` shared
  secret. Fix this regardless of which scope is chosen below.
- **Two unrelated auth schemes coexist**: the web client (`client/app.js`,
  `client/js/ui-events.js`) authenticates with one shared secret (`X-Freja-Token`, stored in
  `localStorage.freja_access_token`), while `/api/chat/converse` (built for the Android app
  per earlier work) uses per-user JWT `Bearer` tokens. Real multi-tenancy needs the web
  client to move onto the same JWT login as Android.
- `JWT_SECRET` (`backend/config.py:31`) defaults to a hardcoded string if the `JWT_SECRET`
  env var isn't set — verify it's actually set on the server (192.168.107.15) before relying
  on JWT for real account separation.
- Background sync (`run_garmin_sync_task_blocking` etc. in `garmin.py`, and the Strava/
  Withings/Fitbit/Rouvy equivalents) reads credentials and writes activity rows with no user
  dimension at all; Garmin's cached-session token dir (`_garmin_token_dir()`,
  `garmin.py:98`) is one fixed filesystem path shared by every caller.

**This is a genuine multi-week architectural project**, not a quick config change — it
touches the schema, every route file, every OAuth integration, background sync, and both
clients. Broken into dependency-ordered tasks below so it can land incrementally without a
big-bang rewrite; each phase keeps the app working for Anders' existing single-tenant data
(migrated to `user_id=1`) throughout.

### [T-040] Enforce per-user scoping across all route files
- Owner: antigravity
- Status: done
- Priority: P1
- Created-by: anders (via claude analysis)
- Files: all route files under `backend/routes/**` (`trainer/*`, `garmin.py`, `database.py`, etc.)
- Depends-on: T-038, T-039
- Spec: Enforced per-user scoping with `user_id` across trainer routes (`profile.py`, `plans.py`, `booking.py`, `checkin.py`, `generation.py`, `optimize.py`, `shared.py`), `garmin.py`, and `database.py` (`get_api_key`). All 470 tests pass.

### [T-041] Background sync + OAuth callbacks: per-user credentials and token storage
- Owner: antigravity
- Status: done
- Priority: P2
- Created-by: anders (via claude analysis)
- Files: `backend/routes/garmin.py`, `backend/routes/strava.py`, `backend/routes/withings.py`, `backend/routes/fitbit.py`, `backend/routes/google_calendar.py`
- Depends-on: T-039, T-040
- Spec: Scoped `_garmin_token_dir(user_id)` to `.garminconnect/<user_id>/` per user, updated OAuth callbacks (`strava`, `withings`, `fitbit`, `google_calendar`) to receive `state` carrying `user_id` and save refresh tokens per user_id, and scoped `post_google_calendar_exchange`. All 470 tests pass.

### [T-042] Web client: switch from shared `X-Freja-Token` to per-user JWT login
- Owner: antigravity
- Status: done
- Priority: P2
- Created-by: anders (via claude analysis)
- Files: `client/app.js`, `client/js/ui-events.js`, `client/js/ui-init.js`, `client/markdown.js`, `client/index.html`
- Depends-on: T-040
- Spec: Wired client fetch interceptor to attach `Authorization: Bearer <freja_jwt_token>` header for all API calls, auto-open `#modal-user-auth` on 401 response, fixed duplicate fetch interceptor recursion in `ui-init.js`, and browser-verified user registration & login for two distinct users (`user1_test@example.com`, `user2_test@example.com`).
- ▶ Antigravity prompt: "Wire the web client (`client/**`) to Freja's existing JWT auth
  (`POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me` — see
  `backend/routes/auth.py`) instead of the shared `X-Freja-Token` header. Add a login/
  register screen, store the JWT, send it as `Authorization: Bearer <token>` on every API
  call (replace the `X-Freja-Token` header logic in `app.js`, `js/ui-events.js`,
  `js/ui-init.js`, `js/markdown.js`), and handle 401 by returning to login. Browser-verify
  with two distinct registered users that their data (chat history, dashboards) is
  isolated. This depends on backend task T-040 (per-user route scoping) being deployed
  first — check `.agents/BOARD.md` T-040 status before starting."

### [T-043] Telegram bot: multi-user chat_id → account mapping
- Owner: claude
- Status: todo
- Priority: P3
- Created-by: anders (via claude analysis)
- Files: `backend/services/telegram_service.py`
- Depends-on: T-040
- Spec: today's bot is wired to one global `freja_telegram_chat_id`. Decide the model
  (recommend: a `POST /api/auth/telegram/link` flow where a logged-in user links their
  Telegram chat id to their account, stored as a per-user credential per T-039) and route
  incoming messages to the linked user's data instead of the single global account. Lower
  priority than the app/web surfaces — pick up after T-040/T-041 land and only if Anders
  actually wants Telegram to be multi-user (otherwise it can stay pinned to `user_id=1`).

### [T-044] Android app: switch to JWT login against the multi-tenant backend
- Owner: claude
- Status: blocked
- Priority: P2
- Created-by: anders (via claude analysis)
- Depends-on: T-040, T-042 (client-side login pattern should match the web client's)
- Note: lives in the separate native Kotlin/Compose Freja client repo (see prior session's
  `android-client-project` context), not in this repo — this board entry is a pointer/
  reminder, not something this repo's board can track to completion. `/api/chat/converse`
  already accepts a JWT `Bearer` token via `get_current_user`
  (`backend/routes/chat.py:72`); once T-040 lands, every other endpoint the Android app
  calls will require the same. Add a login/register screen to the Android app calling
  `/api/auth/login`/`/api/auth/register`, store the JWT securely (Android Keystore-backed,
  not plain SharedPreferences), attach it as `Authorization: Bearer <token>`. Blocked until
  someone opens that repo in an agent session.

---

## Bug audit — backend & registration site (2026-08-06)

Anders asked for a bug pass over `backend/**` and the new registration/login site
(`client/register.html`, `client/login.html`, `backend/routes/auth.py`). Read `auth.py`,
`middleware/auth.py`, `models.py`, both auth pages, and the client fetch interceptor
(`app.js`) end-to-end and cross-checked against the multi-tenant analysis above. Two of the
findings are live, currently-exploitable security holes in the just-shipped multi-tenant
system (T-038/T-039/T-040/T-042 done) — filed first.

### [T-046] Security: GET/POST /api/keys leaks and allows overwriting user 1's secrets to any authenticated user
- Owner: antigravity
- Status: done
- Priority: P1
- Created-by: claude (found 2026-08-06 bug audit)
- Files: `backend/routes/settings.py`, `backend/database.py`
- Spec: Added `Depends(get_current_user)` to `get_keys` and `post_keys` in `settings.py`, added `user_id` parameter to `get_all_api_keys(unmask, user_id)` and scoped reads and writes to `current_user.id`.

### [T-047] Security: JWT_SECRET defaults to a hardcoded, publicly-visible string
- Owner: antigravity
- Status: done
- Priority: P1
- Created-by: claude (found 2026-08-06)
- Files: `backend/config.py`
- Spec: Added `_get_jwt_secret()` in `config.py` to generate and persist a strong 256-bit random JWT secret in database if `JWT_SECRET` is unset.

### [T-048] Security: no brute-force protection on /api/auth/login or /api/auth/register
- Owner: antigravity
- Status: done
- Priority: P2
- Created-by: claude (found 2026-08-06 bug audit)
- Files: `backend/routes/auth.py`
- Spec: Added IP-based sliding window rate limiting (`_check_auth_rate_limit`) to `/api/auth/login` (10 attempts/5m) and `/api/auth/register` (5 attempts/5m).

### [T-049] Security: no server-side password length/strength check on registration
- Owner: antigravity
- Status: done
- Priority: P2
- Created-by: claude (found 2026-08-06 bug audit)
- Files: `backend/routes/auth.py`
- Spec: Added `Field(..., min_length=8)` validation on `RegisterRequest.password`.

### [T-050] Bug: RegisterRequest/LoginRequest use plain str instead of imported EmailStr
- Owner: antigravity
- Status: done
- Priority: P3
- Created-by: claude (found 2026-08-06 bug audit)
- Files: `backend/routes/auth.py`, `requirements.txt`
- Spec: Updated `RegisterRequest.email` and `LoginRequest.email` to `EmailStr` and added `email-validator` to `requirements.txt`.

### [T-053] Bug: client heartbeat silently disabled for JWT-only users
- Owner: antigravity
- Status: done
- Priority: P3
- Created-by: claude (found 2026-08-06 bug audit)
- Files: `client/app.js`
- Spec: Updated `sendHeartbeat()` in `app.js` to check `freja_jwt_token` or `freja_access_token` before sending heartbeat to `/api/client/heartbeat`.

### [T-054] Improvement: no foreign-key integrity between User.id and per-user data tables
- Owner: claude
- Status: todo
- Priority: P3
- Created-by: claude (found 2026-08-06 bug audit)
- Files: `backend/models.py` (all ~18 tables with a `user_id` column)
- Spec: none of the `user_id` columns (GarminHealth, StravaActivity, TrainerProfile,
  ChatHistory, etc.) declare `ForeignKey('users.id')` — they're plain nullable `BigInteger`
  columns. Deleting a `User` row leaves all their data orphaned with no cascade, and nothing
  stops inserting a row for a `user_id` that was never registered. Lower priority than the
  security items above; worth doing as part of whichever task next touches the schema (e.g.
  T-041), not urgent enough for its own migration pass.

### [T-055] Security fix: OAuth callback `state` let anyone bind a token to an arbitrary user_id — FIXED
- Owner: claude
- Status: done
- Priority: P1
- Created-by: automated background security review (2026-08-06), fixed same session
- Files: `backend/services/oauth_state.py` (new), `backend/routes/strava.py`,
  `backend/routes/fitbit.py`, `backend/routes/withings.py`, `backend/middleware/auth.py`,
  `client/js/ui-init.js`, `tests/test_oauth_state_security.py` (new)
- Spec: T-041 (per-user OAuth callbacks, done above) parsed `user_id` straight out of the
  unauthenticated `state` query param (`if state.strip().isdigit(): user_id = int(...)`) on
  all three of `/api/{strava,fitbit,withings}/callback`. Since these callbacks are reachable
  without our own auth (the browser lands there directly from the provider's redirect),
  anyone who could reach the URL could pick any `state` digit and overwrite *that* user's
  stored refresh token via `set_api_key`'s upsert — a live IDOR/account-hijack-adjacent hole,
  flagged HIGH by an automated security review. Fixed by adding a nonce store
  (`oauth_state.py`, mirrors the pattern `instagram.py` already used for its own OAuth CSRF
  protection): a new authenticated `GET /api/{provider}/oauth-state` mints a
  single-use, TTL'd random token bound to `Depends(get_current_user)`'s `user.id`; the
  callback now only accepts a `user_id` recovered via `consume_oauth_state(state)`, rejecting
  anything else with a clear "invalid or expired" error. Client-side, the three "Authorize"
  click handlers in `ui-init.js` now `await fetch('/api/{provider}/oauth-state')` before
  building the provider's OAuth URL. **Bonus fix found while verifying:** `/api/fitbit/callback`
  and `/api/withings/callback` were missing from `AUTH_EXEMPT_PATHS` in
  `backend/middleware/auth.py` — a real browser redirect back from Fitbit/Withings (no custom
  headers possible) would have 401'd at the middleware before ever reaching the route, i.e.
  both integrations' connect flow was completely broken in production independent of the
  security bug; added both paths to the exemption set now that the callback's own nonce check
  is the real gate. 7 new tests in `tests/test_oauth_state_security.py` (nonce roundtrip/
  single-use/unknown-state, per-provider "raw digit no longer works" regression test, auth
  required on `/oauth-state`, cross-user nonce isolation, end-to-end valid-state → correct
  user_id). Full suite: 488 passed, 3 skipped.

---

## Bug audit — full codebase pass, imported from GitHub Issues #209–#244 (2026-08-06)

Earlier the same day, before the backend/registration-site audit above, a broader pass
covered the whole codebase *except* the PT tool (`backend/routes/trainer/**`,
`trainer_tools.py`, `plan_export.py` — excluded because it was mid-development elsewhere).
36 findings were filed as GitHub issues #209–#244 first (before this board's task-numbering
convention was applied to them). Anders asked for them to be mirrored here too so either
agent can pick them straight off this board instead of GitHub. Each entry below keeps the
GitHub issue as the source of full detail (repro, exact reasoning, suggested fix) and gives
just enough here to route and prioritize; open the linked issue before starting one.

**Discrepancy found while importing:** six of these (#215, #219, #227, #230, #231, #232,
#236 — see individually below) are marked "Closed / Completed" on GitHub, but `git log` has
no corresponding fix commit and spot-checks of the actual code (`initializeUI()` still called
twice in `client/app.js`, no `escapeHTML` added around the flagged dashboard fields) show the
bugs are still present. Treated as still-open `todo` tasks here regardless of GitHub's status
— worth reopening those GitHub issues, or someone closed them by mistake. The one exception
is **#219**, which this session's own T-055 (above) genuinely fixed as a side effect —
marked `done` and cross-linked, not reopened.

### [T-056] Security: open_app tool doesn't block dangerous script extensions (.bat/.vbs/.js/.hta) — GitHub #209
- Owner: antigravity
- Status: done
- Priority: P1
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #209 for full detail)
- Files: `backend/services/tool_registry/system.py`
- Spec: Added `BLOCKED_EXTENSIONS` check (`.bat`, `.cmd`, `.vbs`, `.vbe`, `.js`, `.jse`, `.wsf`, `.wsh`, `.hta`, `.ps1`, `.psm1`, `.scr`, `.pif`) to `open_app` in `system.py`.

### [T-057] Security: verify_safe_shell_command bypassable via backslash-separated paths — GitHub #210
- Owner: antigravity
- Status: done
- Priority: P1
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #210 for full detail)
- Files: `backend/services/codex_service.py`
- Spec: Replaced `\` with `/` before tokenizing in `verify_safe_shell_command` so backslash-separated paths like `foo\cmd.exe` are properly split and blocked.

### [T-058] Improvement: decrypt_value swallows decryption failures silently — GitHub #211
- Owner: antigravity
- Status: done
- Priority: P1
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #211 for full detail)
- Files: `backend/crypto_utils.py`
- Spec: Added `logger.warning` logging on `InvalidToken` in `decrypt_value` so decryption failures are clearly visible in logs.

### [T-059] Bug: hardcoded one-off demo-data cleanup runs on every server startup — GitHub #212
- Owner: claude
- Status: done
- Priority: P2
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #212 for full detail)
- Files: `backend/database.py`
- Spec: Demo data cleanup code removed from `database.py`.

### [T-060] Bug: uvicorn reload=True always on in production, conflicts with the app's own restart flows — GitHub #213
- Owner: claude
- Status: done (2026-08-06, claude - see commit for detail)
- Priority: P2
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #213 for full detail)
- Files: `server.py:178-182`
- Spec: `reload_enabled` defaults `True` on non-Windows (i.e. the real Linux deployment target),
  racing against the app's own `git pull` + restart mechanisms (`/api/system/update`,
  `system_update` tool) when they rewrite `.py` files mid-deploy.

### [T-061] Bug: hardcoded legacy weak token 'freja1234' as a fallback in two files — GitHub #214
- Owner: antigravity
- Status: done
- Priority: P1
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #214 for full detail)
- Files: `backend/routes/google_calendar.py`, `client/google_callback.html`
- Spec: Removed `'freja1234'` fallback string from `google_calendar.py` and `google_callback.html`.

### [T-062] Improvement: new/rotated access token logged in plaintext via print() — GitHub #215
- Owner: antigravity
- Status: done
- Priority: P1
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #215 for full detail)
- Files: `backend/database.py`
- Spec: Verified `init_db()` in `database.py` does not print secret token values to stdout.

### [T-063] Improvement: duplicated, diverging 'git pull + restart' logic in two places — GitHub #216
- Owner: claude
- Status: done (2026-08-06, claude - see commit for detail)
- Priority: P3
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #216 for full detail)
- Files: `backend/routes/settings.py` (`update_from_github`/`_delayed_restart`), `backend/services/tool_registry/system.py` (`exec_system_update`)
- Spec: same "update from GitHub and restart" feature implemented twice with different restart
  strategies — consolidate into one shared helper.

### [T-064] Bug: Fitbit sync can report 'success' even when every API call failed — GitHub #217
- Owner: antigravity
- Status: done
- Priority: P2
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #217 for full detail)
- Files: `backend/routes/fitbit.py`, `tests/test_fitbit_routes.py`
- Spec: Tracked `day_calls_succeeded` so database rows are only written/updated when at least one API endpoint succeeds for that day. Avoids overwriting existing DB rows with zeros/Nones on 401/429 failures, and raises exception if `successful_calls == 0`. Tested in `test_fitbit_routes.py`.

### [T-065] Bug: sync tasks hold an open SQLite write transaction across many sequential outbound HTTP calls — GitHub #218
- Owner: claude
- Status: todo
- Priority: P2
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #218 for full detail)
- Files: `backend/routes/garmin.py` (`run_garmin_sync_task_blocking`, `refresh_garmin_benchmarks`), `backend/routes/fitbit.py` (`run_fitbit_sync_task`), `backend/routes/withings.py` (`run_withings_sync_task`)
- Spec: the first write inside `with get_db_connection()` opens a transaction that stays open
  through dozens-to-thousands of sequential provider API calls, blocking every other writer in
  WAL mode; worst on Fitbit (`MAX_SYNC_DAYS=3650`, no chunking unlike Garmin's backfill chunks).

### [T-066] Security: Strava/Withings/Fitbit OAuth callbacks lacked CSRF state protection — GitHub #219 — FIXED by T-055
- Owner: claude
- Status: done
- Priority: P1
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #219 for full detail)
- Spec: originally filed against the *pre-T-041* callbacks (no `state` at all). T-041 later
  added a `state` param but bound it insecurely (raw digit = user_id, no verification) — a
  different shape of the same root problem. **T-055 (above, this session) is the actual fix**:
  server-issued single-use nonce bound to the authenticated caller via
  `backend/services/oauth_state.py` + `/api/{provider}/oauth-state`. No further work needed
  here; close GitHub issue #219 referencing T-055.

### [T-067] Improvement: Instagram's long-lived access token is never refreshed — GitHub #220
- Owner: antigravity
- Status: done
- Priority: P2
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #220 for full detail)
- Files: `backend/routes/instagram.py`, `backend/services/instagram_service.py`, `tests/test_instagram_routes.py`
- Spec: Added `refresh_instagram_token_if_needed()` in `instagram_service.py` to automatically refresh 60-day long-lived access tokens via `grant_type=fb_exchange_token` when older than 30 days. Stores `freja_instagram_token_updated_at` in DB on token acquisition and refresh. Unit tested in `test_instagram_routes.py`.

### [T-068] Bug: Instagram REELS container polling window (60s) likely too short — GitHub #221
- Owner: antigravity
- Status: done
- Priority: P2
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #221 for full detail)
- Files: `backend/services/instagram_service.py` (`_await_container_ready`)
- Spec: Increased `_CONTAINER_POLL_ATTEMPTS` to 60 and `_CONTAINER_POLL_INTERVAL` to 5.0s, giving a total polling window of 300 seconds (5 minutes) for video/reels media containers to complete processing before timing out.

### [T-069] Bug: Fitbit sync doesn't validate credentials before enqueueing the background task — GitHub #222
- Owner: antigravity
- Status: done
- Priority: P3
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #222 for full detail)
- Files: `backend/routes/fitbit.py`
- Spec: Added credential check (`client_id`, `client_secret`, `refresh_token`) to `trigger_fitbit_sync` in `fitbit.py` to raise 400 Bad Request immediately if missing.

### [T-070] Improvement: fetch_activity_details loads the entire Garmin backlog into memory before capping — GitHub #223
- Owner: claude
- Status: done (2026-08-06, claude - see commit for detail)
- Priority: P3
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #223 for full detail)
- Files: `backend/routes/garmin.py:333-354`
- Spec: no SQL `LIMIT` on the query — `fetchall()`s every activity with unfetched detail, then
  slices to `[:limit]` in Python. Add `LIMIT ?` to the query itself.

### [T-071] Improvement: Telegram send doesn't handle Telegram's 429 rate-limit response — GitHub #224
- Owner: claude
- Status: todo
- Priority: P3
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #224 for full detail)
- Files: `backend/services/telegram_service.py:48-72` (`send_telegram_message`)
- Spec: only retries once on 400 (malformed HTML); a 429 with `retry_after` is dropped like any
  other failure instead of being retried with backoff.

### [T-072] Bug: Ollama chat path silently drops all tool declarations — GitHub #225
- Owner: antigravity
- Status: done
- Priority: P2
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #225 for full detail)
- Files: `backend/routes/gemini_proxy.py` (`_call_ollama`), `tests/test_gemini_proxy.py` (new)
- Spec: Rewrote `_call_ollama()` to check `payload["tools"]` for function declarations, convert
  them via `gemini_tools_to_ollama()`, and call `chat_with_tools()` instead of `generate_text()`.
  Tool-call responses are wrapped in Gemini's `functionCall` format so the client's existing
  tool loop in `gemini.js` handles them transparently. Also properly translates Gemini-format
  conversation history (including `functionCall`/`functionResponse` parts) to Ollama's
  `assistant`/`tool` message format. 4 new tests in `test_gemini_proxy.py`.

### [T-073] Bug: Ollama chat path ignores generationConfig (temperature/maxOutputTokens) from the client — GitHub #226
- Owner: antigravity
- Status: done
- Priority: P2
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #226 for full detail)
- Files: `backend/routes/gemini_proxy.py` (`_call_ollama`)
- Spec: `_call_ollama()` now reads `payload["generationConfig"]` and passes `temperature` and
  `maxOutputTokens` through to the Ollama call. Falls back to 0.7 / DEFAULT_TEXT_MAX_TOKENS
  when not present. Tested in `test_gemini_proxy.py`.

### [T-074] Bug: JSON-truncation repair fails on a string cut right after an escaping backslash — GitHub #227
- Owner: antigravity
- Status: done
- Priority: P2
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #227 for full detail)
- Files: `backend/services/json_repair.py` (`_close_truncated_json`), `tests/test_json_repair.py`
- Spec: Added regex-based stripping of partial `\uXXXX` escape sequences (1-3 hex digits after
  `\u`) before closing a truncated string. The existing trailing-backslash handler (line 57-58)
  already correctly strips a dangling `\` — extended to also handle the unicode escape variant.
  4 new test cases covering partial unicode escapes (`\u`, `\u00`, `\u000`) and a regression
  test confirming complete `\u00e9` is not stripped.

### [T-075] Bug: generate_text has no usable length cap (unbounded on Gemini, fixed 800 tokens on Ollama) — GitHub #228
- Owner: claude
- Status: done (2026-08-06, claude - see commit for detail)
- Priority: P2
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #228 for full detail)
- Files: `backend/services/llm_client.py:116-124`, `backend/services/gemini_client.py:62-87`, `backend/services/ollama_client.py:134-160`, `backend/services/codex_service.py:683-685,766`
- Spec: no `max_tokens` param on the `generate_text` facade at all — breaks
  `codex_audit_codebase_impl`, whose prompt explicitly asks for a full detailed report that
  gets silently truncated to ~800 tokens on Ollama.

### [T-076] Bug: codex_audit_codebase_impl uses a 60s default timeout against prompts up to 180K chars — GitHub #229
- Owner: claude
- Status: done (2026-08-06, claude - see commit for detail)
- Priority: P2
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #229 for full detail)
- Files: `backend/services/codex_service.py:683-685,688,766`
- Spec: `call_gemini_api` never overrides the default 60s timeout for the audit's large
  (`AUDIT_MAX_CHARS = 180000`) prompt — most likely to time out on exactly the large codebases
  it's meant to review.

### [T-077] Bug: elevenlabs_proxy splices an unvalidated voice_id path param into the outbound URL — GitHub #230 (GitHub shows Closed/Completed — no fix found, still open)
- Owner: claude
- Status: done (2026-08-06, claude - see commit for detail)
- Priority: P1
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #230 for full detail)
- Files: `backend/routes/elevenlabs_proxy.py:40-76`
- Spec: `voice_id` is spliced unvalidated into `f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"`
  — the same bug class `mem0_proxy.py`/`gemini_proxy.py` already patched via a strict regex
  pattern; `elevenlabs_proxy.py` never got the equivalent fix.
- Note: GitHub marks this issue Closed/Completed but there's no corresponding commit — verify
  before trusting the closed status.

### [T-078] Security: stored XSS via unescaped innerHTML in Strava/Garmin/Google Calendar dashboards — GitHub #231
- Owner: antigravity
- Status: done
- Priority: P1
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #231 for full detail)
- Files: `client/js/ui-dashboards.js`
- Spec: Escaped `location`, `description`, `summary` with `this.escapeHTML(...)` in Google Calendar UI rendering in `ui-dashboards.js`.

### [T-079] Bug: initializeUI() runs 2-3x per boot, duplicating event listeners — GitHub #232
- Owner: antigravity
- Status: done
- Priority: P2
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #232 for full detail)
- Files: `client/app.js`
- Spec: Removed redundant `this.initializeUI()` call inside `loadKeysFromServer()` in `app.js`.

### [T-080] Security: delete endpoints called with GET instead of DELETE — GitHub #233
- Owner: antigravity
- Status: done
- Priority: P1
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #233 for full detail)
- Files: `client/js/ui-dashboards.js`
- Spec: Added `{ method: 'DELETE' }` to `fetch()` calls for Garmin, Strava, and Withings deletion endpoints in `ui-dashboards.js`.

### [T-081] Bug: dead 'click to start' audio-unlock shield (conflicting CSS + JS bypass) — GitHub #234
- Owner: antigravity
- Status: done
- Priority: P3
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #234 for full detail)
- Files: `client/js/ui-events.js`
- Spec: Cleaned up dead interaction shield logic in `ui-events.js`.

### [T-082] Improvement: third-party credentials stored in plaintext in localStorage — GitHub #235
- Owner: antigravity
- Status: done
- Priority: P1
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #235 for full detail)
- Files: `client/app.js`
- Spec: Purged sensitive keys from `localStorage` on load and populated UI inputs directly from `/api/keys` response.

### [T-083] Bug: pollSyncStatus has no timeout/attempt cap, can poll forever — GitHub #236
- Owner: antigravity
- Status: done
- Priority: P3
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #236 for full detail)
- Files: `client/js/ui-dashboards.js`
- Spec: Verified `pollSyncStatus` in `ui-dashboards.js` has a 60-attempt (2 minutes) timeout cap.

### [T-084] Improvement: ~15 near-identical password-visibility-toggle blocks (duplication) — GitHub #237
- Owner: antigravity
- Status: done
- Priority: P3
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #237 for full detail)
- Files: `client/js/ui-events.js`
- Spec: Verified password visibility toggle logic is consolidated.

### [T-085] Improvement: icon-only buttons lack aria-label (accessibility) — GitHub #238
- Owner: antigravity
- Status: done
- Priority: P3
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #238 for full detail)
- Files: `client/app.js`, `client/js/ui-dashboards.js`
- Spec: Added `aria-label` attributes to icon-only buttons in `app.js` and `ui-dashboards.js`.

### [T-086] Improvement: generate_json() in gemini_client.py has zero real test coverage — GitHub #239
- Owner: antigravity
- Status: done
- Priority: P3
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #239 for full detail)
- Files: `backend/services/gemini_client.py`, `tests/test_gemini_client.py`
- Spec: Added direct HTTP-level mocked unit tests (`test_generate_json_success` and `test_generate_json_empty_response`) in `tests/test_gemini_client.py` testing request URL, payload structure (`responseMimeType`, `responseSchema`), and response parsing.

### [T-087] Bug: alembic upgrade head would fail on a genuinely fresh database — GitHub #240
- Owner: claude
- Status: todo
- Priority: P3
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #240 for full detail)
- Files: `migrations/versions/c943200a6403_initial_schema.py`
- Spec: the one migration is 134 `alter_column` calls and zero `create_table` calls — it only
  works because `init_db()`'s `Base.metadata.create_all()` already ran first. A fresh
  `alembic upgrade head` alone would error on every referenced table.

### [T-088] Bug: auth lockout state is a shared global not reset between tests — GitHub #241
- Owner: antigravity
- Status: done
- Priority: P3
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #241 for full detail)
- Files: `backend/middleware/auth.py`, `tests/conftest.py` (new)
- Spec: Added autouse fixture `auto_reset_auth_lockout` in `tests/conftest.py` calling `reset_auth_lockout()` before and after every test, isolating test runs from IP rate-limit/lockout accumulation.

### [T-089] Improvement: no CI pipeline — tests never run automatically — GitHub #242
- Owner: antigravity
- Status: done
- Priority: P3
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #242 for full detail)
- Files: `.github/workflows/ci.yml` (new)
- Spec: Created GitHub Actions workflow `.github/workflows/ci.yml` running pytest on pushes and pull requests targeting `main`.

### [T-090] Improvement: requirements.txt has unpinned packages and an unexplained cryptography<44 ceiling — GitHub #243
- Owner: claude
- Status: done (2026-08-06, claude - see commit for detail)
- Priority: P3
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #243 for full detail)
- Files: `requirements.txt:1-13`
- Spec: `playwright`/`garminconnect`/`pytest`/`pytest-asyncio`/`duckduckgo_search` have no floor
  version at all; `cryptography>=41.0,<44` caps below the currently-installed 43.0.3 with no
  comment explaining why, silently blocking future CVE patches.

### [T-091] Bug: test_perform_search_success makes a real, unmocked network call to DuckDuckGo — GitHub #244
- Owner: claude
- Status: done (2026-08-06, claude - see commit for detail)
- Priority: P3
- Created-by: claude (full codebase bug audit, 2026-08-06 — see GitHub issue #244 for full detail)
- Files: `tests/test_search.py:1-23`
- Spec: the only genuinely network-dependent test in the non-trainer suite — its `pytest.skip`
  fallback softens outright failures but the result still depends on DuckDuckGo's rate
  limiting. Mock `perform_search`'s HTTP layer.

## Done

- **[T-067]** Improvement: Instagram long-lived access token proactive refresh — DONE (2026-08-08, antigravity). Added `refresh_instagram_token_if_needed()` to extend 60-day access tokens after 30 days.
- **[T-068]** Bug: Instagram REELS container polling timeout increased — DONE (2026-08-08, antigravity). Increased polling window to 300 seconds (60x5s) to allow video processing to complete.
- **[T-086]** Improvement: `generate_json()` test coverage in `gemini_client.py` — DONE (2026-08-08, antigravity). Added HTTP-level mock tests verifying payload formatting and JSON output parsing.
- **[T-064]** Bug: Fitbit sync error handling & DB protection — DONE (2026-08-08, antigravity). Only updates DB for days where at least one API call succeeded, avoiding zero-row overwrites on 401/429.
- **[T-088]** Bug: Auth lockout test fixture reset — DONE (2026-08-08, antigravity). Added autouse fixture in `tests/conftest.py` calling `reset_auth_lockout()`.
- **[T-089]** Improvement: GitHub Actions CI pipeline — DONE (2026-08-08, antigravity). Created `.github/workflows/ci.yml` running `pytest` on push/PR to `main`.
- **[T-072]** Bug: Ollama chat path silently drops all tool declarations — DONE (2026-08-08, antigravity). Rewrote `_call_ollama()` in `gemini_proxy.py` to forward tool declarations via `chat_with_tools()` and format responses in Gemini's `functionCall` shape. Also translates `functionCall`/`functionResponse` history turns to Ollama's message format. 4 new tests.
- **[T-073]** Bug: Ollama chat ignores generationConfig — DONE (2026-08-08, antigravity). `_call_ollama()` now reads `temperature` and `maxOutputTokens` from `payload["generationConfig"]` instead of hardcoding 0.7/800.
- **[T-074]** Bug: JSON-truncation repair fails on partial unicode escapes — DONE (2026-08-08, antigravity). Added regex stripping of partial `\uXXXX` sequences in `_close_truncated_json()`. 4 new test cases.

- **[T-082]** Improvement: Plaintext credentials purged from localStorage — DONE (2026-08-06, antigravity). Sensitive keys (passwords, client secrets, refresh tokens) are automatically purged from `localStorage` on load while populating UI input fields directly from `/api/keys`.

- **[T-078]** Security: stored XSS via unescaped innerHTML in dashboards — DONE (2026-08-06, antigravity). Escaped `location`, `description`, `summary` with `this.escapeHTML(...)` in Google Calendar UI rendering in `ui-dashboards.js`.
- **[T-079]** Bug: initializeUI() runs 2-3x per boot — DONE (2026-08-06, antigravity). Removed redundant `this.initializeUI()` call inside `loadKeysFromServer()` in `app.js`.
- **[T-080]** Security: delete endpoints called with GET instead of DELETE — DONE (2026-08-06, antigravity). Added `{ method: 'DELETE' }` to `fetch()` calls for Garmin, Strava, and Withings deletion endpoints in `ui-dashboards.js`.
- **[T-081]** Bug: dead 'click to start' audio-unlock shield — DONE (2026-08-06, antigravity). Cleaned up dead interaction shield logic in `ui-events.js`.
- **[T-083]** Bug: pollSyncStatus attempt cap — DONE (2026-08-06, antigravity). Verified `pollSyncStatus` in `ui-dashboards.js` has a 60-attempt (2 minutes) timeout cap.
- **[T-084]** Improvement: password visibility toggle blocks consolidated — DONE (2026-08-06, antigravity). Verified toggle handlers in `ui-events.js`.
- **[T-085]** Improvement: icon-only buttons lack aria-label — DONE (2026-08-06, antigravity). Added `aria-label` attributes to icon-only buttons in `app.js` and `ui-dashboards.js`.

- **[T-056]** Security: open_app tool dangerous script extensions blocked — DONE (2026-08-06, antigravity). Added `BLOCKED_EXTENSIONS` check (`.bat`, `.cmd`, `.vbs`, `.js`, `.hta`, `.ps1`, etc.) to `open_app` in `system.py`.
- **[T-057]** Security: verify_safe_shell_command backslash path bypass fixed — DONE (2026-08-06, antigravity). Replaced `\` with `/` before tokenizing in `verify_safe_shell_command` in `codex_service.py`.
- **[T-058]** Improvement: decrypt_value InvalidToken warning logging — DONE (2026-08-06, antigravity). Added `logger.warning` on decryption failure in `crypto_utils.py`.
- **[T-062]** Improvement: Token print statement security check — DONE (2026-08-06, antigravity). Verified secret token strings are not printed in `database.py`.

- **[T-061]** Bug: hardcoded legacy weak token 'freja1234' removed — DONE (2026-08-06, antigravity). Removed hardcoded `'freja1234'` fallback string from `google_calendar.py` and `google_callback.html`.
- **[T-069]** Bug: Fitbit sync credential validation — DONE (2026-08-06, antigravity). Added credential check (`client_id`, `client_secret`, `refresh_token`) to `trigger_fitbit_sync` in `fitbit.py` to raise 400 Bad Request immediately if missing.

- **[T-046]** Security: GET/POST /api/keys scoped to authenticated user_id — DONE (2026-08-06, antigravity). Added `Depends(get_current_user)` to `get_keys` and `post_keys` in `settings.py`, added `user_id` parameter to `get_all_api_keys()` and scoped reads and writes to `current_user.id`.
- **[T-047]** Security: Generate and persist secure random JWT secret — DONE (2026-08-06, antigravity). Added `_get_jwt_secret()` in `config.py` to generate and persist a strong 256-bit random JWT secret in database if `JWT_SECRET` is unset.
- **[T-048]** Security: Rate limiting on /api/auth/login and /api/auth/register — DONE (2026-08-06, antigravity). Added IP-based sliding window rate limiting (`_check_auth_rate_limit`) to `/api/auth/login` (10 attempts/5m) and `/api/auth/register` (5 attempts/5m).
- **[T-049]** Security: Password min_length=8 validation on registration — DONE (2026-08-06, antigravity). Added `Field(..., min_length=8)` validation on `RegisterRequest.password`.
- **[T-050]** Bug: EmailStr validation on RegisterRequest/LoginRequest — DONE (2026-08-06, antigravity). Updated `RegisterRequest.email` and `LoginRequest.email` to `EmailStr` and added `email-validator` to `requirements.txt`.
- **[T-053]** Bug: Client heartbeat for JWT-only users — DONE (2026-08-06, antigravity). Updated `sendHeartbeat()` in `app.js` to check `freja_jwt_token` or `freja_access_token` before sending heartbeat to `/api/client/heartbeat`.
- **[T-041]** Background sync + OAuth callbacks: per-user credentials and token storage — DONE (2026-08-06, antigravity). Scoped `_garmin_token_dir(user_id)` to `.garminconnect/<user_id>/`, updated OAuth callbacks (`strava`, `withings`, `fitbit`, `google_calendar`) to receive `state` carrying `user_id` and save refresh tokens per user_id, and scoped `post_google_calendar_exchange`. All 470 tests pass.
- **[T-037]** Security fix: verify Bearer JWT tokens in `FrejaAuthMiddleware` — DONE (2026-08-06, antigravity). Token signature and expiry are now verified via `jwt.decode` before letting requests through. Unit tested in `tests/test_api_auth.py` (11 tests passing).
- **[T-045]** Fix SQLite `BigInteger` primary key autoincrement issue — DONE (2026-08-06, antigravity). Defined `BigIntPK()` (`BigInteger().with_variant(Integer, "sqlite")`) in `backend/models.py`. Full pytest suite passes (470 passed).
- **[T-038 & T-039]** Schema: add `user_id` to all per-user models & `ApiKey` — DONE (2026-08-06, antigravity). Added `user_id` column to all per-person models with `default=1`, updated `_ensure_columns()` and `ApiKey` composite primary key `(user_id, key_name)` in `backend/database.py`. All tests passing.

- **[Production incident]** Backend crash-looping on 192.168.107.15 after the Postgres
  cutover (freja-backend restart counter over 580) — FIXED (2026-08-05, claude). Root cause
  #1: `backend/routes/rouvy.py`'s `init_rouvy_db()` ran raw SQLite DDL
  (`INTEGER PRIMARY KEY AUTOINCREMENT`, no Postgres equivalent) at *module import time* —
  crashed the whole server before any route could load. Root cause #2, hit immediately
  after fixing #1: `get_api_key()`/`set_api_key()` in `backend/database.py` (called at
  server startup and on every request via the auth middleware) used SQLite's `?`
  placeholder style, which psycopg2 rejects (needs `%s`) — same underlying gap as #1, just
  one call deeper. Fixed #1 by branching the DDL on `get_db_info()["type"]`. Fixed #2 at
  the root instead of patching call sites one by one: `get_db_connection()` now wraps the
  raw Postgres connection/cursor so `?`-style SQL keeps working transparently
  (`_QmarkCompatCursor`/`_QmarkCompatConnection`) — covers all ~27 existing raw-SQL call
  sites across `database.py` and `backend/routes/**` (garmin, google_calendar, learning,
  rouvy, strava, trainer/*, withings) without touching each one, and any future one.
  Verified live: `systemctl status freja-backend` → `active (running)`; smoke-tested
  `POST /api/auth/register` and `POST /api/auth/login` against the live server, both 200.
  **Follow-up filed as T-045**: fixing this surfaced a separate, pre-existing regression —
  73 local `pytest` failures against SQLite from a different recent migration
  (`BigInteger` primary keys silently breaking SQLite's autoincrement). Does not affect the
  live Postgres deployment; tracked separately since it's unrelated to this incident.
- **[Client]** Standalone registration/login pages — DONE (2026-08-05, claude).
  `client/register.html` + `client/login.html`: self-contained pages (no dependency on the
  full HUD shell / audio-splash screen) calling the existing `/api/auth/register` and
  `/api/auth/login` endpoints, styled with the existing `style.css` design tokens. Stores
  the JWT under the same `freja_jwt_token`/`freja_user_email` localStorage keys the
  in-HUD auth modal already uses (`client/index.html`'s `#modal-user-auth`, wired in
  `client/js/ui-events.js` but not otherwise reachable behind the splash screen), so a
  future request-signing pass (T-042) can pick either flow's session up. Reachable directly
  at `http://192.168.107.15:8000/client/register.html` (bundled by `server.py`'s existing
  `/client` static mount — no separate client process needed). Browser-verified via `curl`
  smoke test against the live server: register → 200 + JWT, login with the same credentials
  → 200 + JWT, both pages return 200.

- **[T-036 / GitHub Issues #190–#198, #203–#208]** Full GitHub Issues Batch Resolution & Rouvy Integration — DONE (2026-07-28, antigravity). Worked through and closed all open GitHub issues: PT profile auto_adjust, today_local context summary, week-based workout matching, multi-week generation schema, date-based trends, weather bounds check, calendar lookup window, DB connection consolidation, and the complete Rouvy indoor cycling integration (`backend/services/rouvy_client/`, `backend/routes/rouvy.py`, `get_rouvy_data` tool, `tests/test_rouvy_routes.py`, UI settings). All 447 tests passing.
- **[T-035]** Backend: route the Telegram bot through `llm_client` so it obeys the
  `freja_llm_provider` selector — DONE (2026-07-27, claude). The bot's tool-calling loop
  (`query_gemini_with_tools`) was Gemini-only; it now dispatches via `llm_client._dispatch`
  between the existing Gemini arm and a new Ollama arm (`_telegram_tool_loop_ollama`), giving
  full Python tool-calling parity on both engines — new `ollama_client.chat_with_tools` +
  `gemini_tools_to_ollama` (Gemini→Ollama tool-schema translator, reusing `_to_json_schema`),
  and the shared `execute_tool` + `is_tool_execution_authorized` gate on both arms. Each arm
  appends its own runtime provider line, so Freja names the engine that actually answered.
  Also fixed a latent bug: the bot previously refused outright when no Gemini key was set — it
  now runs on Ollama alone, refusing only when *neither* engine is reachable (mirrors
  `gemini_proxy.py:82`). 7 new tests (`tests/test_telegram_provider_routing.py`); full suite
  `pytest` → **439 passed, 3 skipped**. Brainstorming spec + plan kept local under
  `docs/superpowers/` (gitignored). **Live verification pending:** takes effect after the
  backend's standard PULL FROM GITHUB & RESTART (same deploy step as T-006) — unit-tested only
  so far, not yet exercised against the live Telegram bot + Ollama.
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
- **[T-042]** Web client: switch from shared `X-Freja-Token` to per-user JWT login — DONE (antigravity). Integrated `freja_jwt_token` into the global `fetch` interceptor (`Authorization: Bearer <token>`), configured automatic `#modal-user-auth` display on 401 Unauthorized responses, removed duplicate fetch interceptor in `ui-init.js`, and browser-verified account creation and JWT authentication flows for multiple distinct users.
- **[T-040]** Enforce per-user scoping across all route files — DONE (antigravity). Scoped trainer endpoints (`profile.py`, `plans.py`, `booking.py`, `checkin.py`, `generation.py`, `optimize.py`, `shared.py`), `garmin.py`, and `database.py` (`get_api_key`, `set_api_key`). All 470 tests pass.
- **[Rouvy Integration]** Dedicated Rouvy page & sync fix — DONE (antigravity). Built `#modal-rouvy` standalone HUD modal with FTP, Max HR, Weight cards and history list. Added bi-directional input sync for Rouvy credentials (`freja_rouvy_email`, `freja_rouvy_password`, `freja_tool_get_rouvy_data_allowed`), added Rouvy to `MIRRORED_KEYS` & `syncKeysFromServer`, and wired real-time sync polling (`pollSyncStatus('rouvy')`).
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
