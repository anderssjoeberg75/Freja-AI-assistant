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

## Garmin/Strava batch — imported from GitHub issues #176–#189 (2026-07-24)

All 14 issues are backend-Python at their core (Garmin/Strava sync, DB schema, prompt
budget) — Claude's lane, not Antigravity's. Where an issue also has a distinct UI
deliverable (a button, card, chart, panel), that piece is split into its own `blocked`
task owned by `antigravity`, unblocked once its parent backend task is `done`.

Suggested build order for claude (dependency-driven, not priority-driven):
**T-011 → T-012 → T-013 → T-014 → T-015 → T-016 → T-017/T-018/T-019 (parallel-ish, all
depend on T-016) → T-020 → T-022 → T-021 → T-023 (coordination, decide tiers before T-013/
T-014/T-016/T-018/T-019/T-020 each add prompt content) → T-010.**

### [T-011] Garmin sync drops every activity after the first one each day
- Owner: claude
- Status: done (2026-07-24)
- Priority: P1
- Created-by: anders (GitHub issue #177)
- Files: `backend/models.py` (`GarminActivity`), `backend/database.py`, `backend/routes/garmin.py`, `backend/routes/trainer/shared.py` (`build_training_load_summary`), `tests/test_garmin_routes.py`
- **DONE.** The `get_activities(0,30)` → `get_activities_by_date` half was already fixed by
  a prior commit (`a2db28c`); this closed the remaining half — the per-day loop still kept
  only the first activity match via `break`. New `GarminActivity` model / `garmin_activities`
  table (unique on `activity_id`, picked up by `Base.metadata.create_all` — no Alembic
  revision needed, matching how `TrainerInjuryLog`/`TrainerStrengthLog` were added). All
  fetched activities are upserted every sync (idempotent, so a backfill-chunk overlap with
  the recent window doesn't duplicate); the per-day rollup into `garmin_health.workout_type`/
  `workout_duration` now uses the day's dominant (longest) session's type and the sum of all
  same-day sessions' minutes, instead of the first match found. `build_training_load_summary()`'s
  Garmin fallback now reads `garmin_activities` instead of the single-session rollup, so a
  multi-session day counts fully. New `GET /api/garmin/activities?days=N`. Two new tests
  (multi-session-day rollup, idempotent re-sync) — `pytest` → 354 passed, 3 skipped.
- Note: old Garmin-sourced sessions predating this change exist only in `garmin_health`'s
  rollup, not in `garmin_activities` — they reappear in `build_training_load_summary()` once
  the account is re-synced. No backfill was in scope for this issue.

### [T-012] Cut Garmin sync request volume via date-ranged endpoints
- Owner: claude
- Status: done (2026-07-24)
- Priority: P1
- Created-by: anders (GitHub issue #178)
- Files: `backend/routes/garmin.py`, `tests/test_garmin_routes.py`
- **DONE, with a scope correction against the issue text.** Inspected the installed
  `garminconnect==0.3.6` source directly rather than trusting the issue's endpoint list at
  face value: `get_body_battery(startdate, enddate)` and `get_daily_steps(start, end)` do
  return one entry **per day** (confirmed via the library's typed models — `BodyBatteryEntry.date`,
  daily steps' `calendarDate`) and are now hoisted once before the per-day loop via a new
  `_index_daily_response()` helper. But `get_weekly_stress`/`get_weekly_intensity_minutes`
  — despite the issue calling them "verified present" replacements — turned out from the
  library's own docstrings to return one **aggregate per week** (`calendarDate` = week
  start, a single `value`/`moderateValue`/`vigorousValue`), not per-day figures. Using them
  as drop-in replacements for the daily `stress_avg`/`stress_max`/`intensity_minutes`
  columns would have silently produced wrong per-day numbers, so those three stay on the
  per-day `get_stats()` call as before. Net result: body battery + steps collapse from
  ~2×30 to 2 requests per 30-day sync; stress/intensity/sleep/HRV/training-status remain
  per-day (no verified daily-ranged endpoint for them). Preserved both required properties:
  each bulk call has its own `try/except` degrading to `{}` (never aborts the sync), and a
  date missing from a bulk response yields `None` via `.get()`, identically to a failed
  per-day call. Updated the 3 existing fake-Garmin-client tests whose stubs used the old
  single-date `get_body_battery`/`get_stats(totalSteps)` shape, plus 2 new tests (ranged
  endpoints called exactly once regardless of window length; a bulk failure degrades to
  `None` without failing the sync). `pytest` → 356 passed, 3 skipped.

### [T-013] Capture Garmin's own training load (CTL/ATL/TSB/ACWR)
- Owner: claude
- Status: done (2026-07-24)
- Priority: P2
- Created-by: anders (GitHub issue #179)
- Files: `backend/models.py`, `backend/database.py`, `backend/routes/garmin.py`, `backend/routes/trainer/shared.py`, `backend/services/tool_registry/health_data.py`, `tests/test_garmin_routes.py`
- **DONE.** 7 new `garmin_health` columns (`training_load_acute/chronic`, `acwr`,
  `acwr_status`, `load_aerobic_low/high`, `load_anaerobic`); TSB is never stored, only
  computed on read (`chronic - acute`) wherever it's surfaced, so it can't drift. New
  `_latest_device_entry()` helper picks the device with the freshest `calendarDate` out of
  the device-keyed `acuteTrainingLoadDTO`/`metricsTrainingLoadBalanceDTOMap` maps —
  deterministic when more than one device has reported. Parsed inside the existing
  `get_training_status()` try block (no extra request). Surfaced in `GET /api/garmin/data`
  (incl. derived `tsb`), `get_garmin_health`'s `latest_metrics`, `build_training_load_summary()`
  (new `latest_load` key), `_format_progression_rules()` (an ACWR guardrail line alongside
  the existing minute ceilings — advisory only, doesn't replace them), and
  `format_training_load_summary()`. Note for T-023: CTL/ATL/ACWR+status and today's planned
  session are Tier A; load-balance vs targets is Tier B (resident only when off-target) —
  the prompt text already reads naturally either way since it's a single conditional line.
  3 new tests (realistic device-keyed payload parses; no-DTO payload stores `None` ×7
  without failing the day; two devices resolve deterministically). `pytest` → 359 passed,
  3 skipped.

### [T-014] Store Garmin Training Readiness score, lead the daily check-in with it
- Owner: claude
- Status: done (2026-07-24)
- Priority: P2
- Created-by: anders (GitHub issue #180)
- Files: `backend/models.py`, `backend/database.py`, `backend/routes/garmin.py`, `backend/routes/trainer/checkin.py`, `backend/routes/trainer/plans.py`, `backend/services/tool_registry/health_data.py`, `tests/test_garmin_routes.py`
- **DONE.** `get_training_readiness` is now called unconditionally every day (previously
  gated behind `if recovery_time is None:`, so the score itself was never stored at all -
  only ever scavenged as a `recovery_time` fallback). The `recovery_time` fallback behavior
  is unchanged, it just no longer decides whether the call happens. 3 new columns
  (`training_readiness`, `training_readiness_level`, `training_readiness_feedback`) added to
  the per-day reset block, `GET /api/garmin/data`, `get_garmin_health`'s `latest_metrics`,
  `build_chat_context_block()` (leads the block, per #189 Tier A), and
  `trainer_daily_checkin`'s Garmin snapshot (leads it, as the issue asked). Did **not**
  switch to `get_morning_training_readiness()` — evaluating it needs a live account reading
  taken at different times of day, which isn't available here; the current
  `get_training_readiness(date_str)` call is kept and this is left as a documented
  open follow-up rather than a blind swap. 2 new tests (score/level/feedback stored even
  when `get_training_status` already supplied `recovery_time` — the un-gating itself, plus
  the list-shaped payload; a day with no readiness record stores `None` ×3 without failing).
  `pytest` → 361 passed, 3 skipped.
- Handoff: **T-025 (HUD readiness card) is now unblocked** — `training_readiness`/`_level`/
  `_feedback` are live on `GET /api/garmin/data`.

### [T-015] Garmin auth: classify token expiry, add re-auth, support MFA
- Owner: claude
- Status: done (2026-07-24) — MFA explicitly deferred, see below
- Priority: P2
- Created-by: anders (GitHub issue #181)
- Files: `backend/routes/garmin.py`, `tests/test_garmin_routes.py`
- **DONE except MFA (deliberately deferred).** `_classify_garmin_error()` distinguishes
  `GarminConnectAuthenticationError` → `auth_required` from `GarminConnectTooManyRequestsError`
  → `rate_limited` from everything else → the existing generic `error` (`sync_status.py`
  needed no change — `sync_states` already accepts arbitrary state strings). Wired into both
  `run_garmin_sync_flow`'s error handler and the new `POST /api/garmin/reauth` (clears the
  tokenstore dir via `_garmin_token_dir()`, now shared with the sync task, and re-logs in).
  `GET /api/garmin/credentials` now also returns `token_age_days` (from the tokenstore's
  newest file mtime) and `token_stale_warning` (age ≥ 150 days, approaching Garmin's ~6
  month lifetime).
  **MFA not implemented** — the installed `garminconnect==0.3.6` genuinely supports it
  (`Garmin(..., return_on_mfa=True)` + `client.resume_login(client_state, code)`, confirmed
  by reading the library source), so this is buildable, not speculative. But it needs (a)
  confirming 2FA is actually enabled on this Garmin account, which nothing here can check,
  and (b) a design for holding `client_state` safely across two HTTP calls (in-memory
  session with a timeout, or similar) — meaningfully bigger than the rest of this issue and
  explicitly gated by the issue itself ("worth confirming... before building it"). Left as an
  open follow-up. Without it, a 2FA-enabled account's reauth attempt fails with a
  classified `auth_required` error rather than completing — a real gap, but a classified
  one instead of a silent hang.
  5 new tests: sync-flow classification (auth/rate-limit/generic, 3 tests), reauth
  clears-and-relogs-in + failure classification (2 tests), token-age warning present/absent
  (2 tests — 7 total). `pytest` → 369 passed, 3 skipped.
- Handoff: **T-026 is now unblocked** for the non-MFA parts (reauth button + token-age
  banner). Its board entry should be trimmed to drop the MFA code-input step, or Antigravity
  can leave that part out of scope for now — MFA has no backend endpoint to call.

### [T-016] Fetch per-activity Garmin detail once per new activity
- Owner: claude
- Status: done (2026-07-24)
- Priority: P2
- Created-by: anders (GitHub issue #182)
- Files: `backend/models.py`, `backend/database.py`, `backend/routes/garmin.py`, `tests/test_garmin_routes.py`
- **DONE.** Nullable `detail_fetched_at` marker on `garmin_activities`;
  `fetch_activity_details(client, cursor, limit=10, since_date=None)` selects unfetched
  rows, fetches via `get_activity()` (singular — verified the plural `get_activity_details()`
  is a different, much heavier endpoint, per the issue), stores into the new
  `garmin_activity_detail` table keyed on `activity_id`, stamps the marker. Capped at 10 per
  call, logs a deferred count rather than reading a capped pass as complete. Runs in
  `run_garmin_sync_flow` after the backfill drain, in its own `try/except` (re-logs in
  fresh, since the health-metric sync's own connection has already closed by that point);
  per-activity `try/except` inside the loop too, so one malformed activity's failure leaves
  only its own `detail_fetched_at` NULL for retry.
  Scope note: skipped storing `activityTrainingLoad` in the detail table — it's already
  captured in `garmin_activities.training_load` from the activity-list payload (#177), so
  the issue's own text calls it "confirms the list value" rather than new data; duplicating
  an identical column across two tables added nothing.
  `since_date` defaults `None` for the automatic per-sync pass called with
  `DETAIL_FETCH_SHIP_DATE = "2026-07-24"` (today), so old history is opt-in, exactly as
  specified; the new `POST /api/garmin/activities/backfill-detail?limit=N` omits the date
  filter for deliberate historical fills, draining the same capped mechanism on repeated
  calls. `GET /api/garmin/activities` now LEFT JOINs the detail table so the fields are
  live wherever activities are already read.
  6 new tests: already-fetched activity triggers no call; a successful fetch stores fields
  and stamps the marker; a failing fetch leaves the marker NULL without aborting the other
  activities in the batch; the cap defers the remainder and reports it; the `since_date`
  filter excludes older activities; the backfill endpoint requires credentials.
  `pytest` → 375 passed, 3 skipped.

### [T-017] Auto-import Garmin strength sets into the PT strength log
- Owner: claude
- Status: done (2026-07-24)
- Priority: P2
- Created-by: anders (GitHub issue #183)
- Files: `backend/models.py`, `backend/database.py`, `backend/routes/garmin.py`, `backend/routes/trainer/shared.py`, `backend/routes/trainer/profile.py`, `backend/services/garmin_exercises.py` (new), `tests/test_garmin_routes.py`, `tests/test_trainer_routes.py`
- **DONE.** `source`/`activity_id` columns added to `trainer_strength_logs`; existing rows
  backfilled to `source='manual'`, and the manual-entry endpoint now writes it explicitly
  going forward. `backend/services/garmin_exercises.py`: bidirectional
  `GARMIN_TO_SWEDISH`/`SWEDISH_TO_GARMIN` table (the second built from the first, so T-010's
  reverse direction can never drift from this one) plus a prettifying fallback for unmapped
  names, logged so the table can grow from what actually shows up.
  New `raw_type_key` column on `garmin_activities` (untranslated Garmin `typeKey`, alongside
  the Swedish-mapped `type`) so the strength-type filter doesn't have to reverse the display
  label. `_import_garmin_strength_sets()` groups active sets by exercise (skips `REST`),
  computing `sets` = active-set count, `reps` = modal rep count with the full per-set
  breakdown in `notes`, `weight` = top working weight in kg, `rpe = NULL`; re-import deletes
  and replaces only that `activity_id`'s own `source='garmin'` rows, never touching manual
  ones. Wired into T-016's `fetch_activity_details()` for
  `{fitness_equipment, strength_training, indoor_cardio}` activities, in its own nested
  try/except so a failure there doesn't un-stamp the (already-succeeded) detail marker or
  count the whole activity as failed. `get_recent_strength_logs()` now dedupes on
  `(date, exercise_name)`, preferring the Garmin row.
  Field-name caveat: `get_activity_exercise_sets` has no typed model in the installed
  client — the exact key names (`exerciseSets`, `exerciseName`, `weight` in grams,
  `setType`) are the issue's best-effort description of an undocumented endpoint, not
  independently verified against a live account. Documented in
  `_import_garmin_strength_sets`'s docstring; a shape mismatch degrades to "no exercises
  imported this run" rather than failing anything.
  8 new tests: two-exercise import with set counts; re-import idempotency; a manual row
  survives; an unmapped name is prettified not dropped; a non-strength activity never calls
  the sets endpoint; a strength activity does, end-to-end through the detail pass; the
  dedup preference in `get_recent_strength_logs`. `pytest` → 382 passed, 3 skipped.
- Handoff: **T-027 (PT-panel source badge) is now unblocked** — `source` is live on
  `GET /api/trainer/strength/log`.
- Handoff: T-027 (PT-panel source badge) is blocked on the `source` column existing.

### [T-018] Capture time-in-HR-zones per session
- Owner: claude
- Status: todo
- Priority: P2
- Created-by: anders (GitHub issue #184)
- Files: `backend/routes/trainer.py` (`build_training_load_summary`, `_format_progression_rules`), `backend/database.py`
- Depends-on: T-016
- Spec: `get_activity_hr_in_timezones(activity_id)` → seconds per zone. New
  `garmin_activity_zones` table keyed on `activity_id`, five `secs_zone_1..5` columns
  (skip activities with no HR data — no row, not a failure). Compute `easy_pct` (zones 1-2)
  / `hard_pct` (zones 4-5) on read, never store them (must not drift). Feed weekly easy/hard
  split into `build_training_load_summary()` and an intensity guardrail next to
  `MAX_SESSION_STEP_PCT`/`MAX_WEEKLY_STEP_PCT` in `_format_progression_rules()`. `GET
  /api/garmin/zones?days=N` for the HUD. Note for #189: weekly easy/hard split is a Tier-B
  field — resident in the prompt only when it deviates from the target band.
- Handoff: T-028 (HUD stacked zone bar) is blocked on `/api/garmin/zones?days=N`.

### [T-019] Capture lap splits, grade adherence on execution not just attendance
- Owner: claude
- Status: todo
- Priority: P2
- Created-by: anders (GitHub issue #185)
- Files: `backend/routes/trainer.py` (`compute_adherence` ~480), `backend/database.py`
- Depends-on: T-016
- Spec: Three steps, each independently useful — land at least step 1 before calling this
  done. **Step 1:** `garmin_activity_laps` table (unique on `activity_id, lap_index`);
  consume `get_activity_splits`, skip `lapCount <= 1`; `GET
  /api/garmin/activities/{id}/laps`. **Step 2:** lap breakdown of recent structured sessions
  into `build_chat_context_block()` (Tier C per #189 — tool-call only, not resident).
  **Step 3:** extend `compute_adherence()` with an execution-quality dimension matching
  work-interval laps (via `get_activity_typed_splits`'s `intensityType`) against the
  prescribed session in `trainer_plans.advice_text`; must degrade to today's date-only
  behaviour whenever a confident match isn't possible, never emit a wrong grade.
- Handoff: T-029 (PT-panel lap table) is blocked on the step-1 endpoint.

### [T-020] Pull Garmin performance benchmarks (threshold, race predictions, PRs)
- Owner: claude
- Status: todo
- Priority: P3
- Created-by: anders (GitHub issue #186)
- Files: `backend/routes/garmin.py` (`run_garmin_sync_flow`), `backend/database.py`, `backend/routes/trainer.py` (plan-generation prompt)
- Spec: Account-level, not per-activity — independent of T-016. `garmin_benchmarks`
  key/value table (`key`, `value`, `unit`, `as_of_date`, `updated_at`) rather than a wide
  table. `refresh_garmin_benchmarks()` self-limited to weekly cadence (mirror
  `recompute_health_baselines()`). Individual `try/except` per benchmark — a missing one
  (no power meter, no compatible watch) is normal, not an error. Start with
  `get_lactate_threshold`, `get_race_predictions`, `get_personal_record`,
  `get_running_tolerance`; then `get_endurance_score`/`get_hill_score`/`get_fitnessage_data`;
  then cached `get_activity_types()` to replace the hardcoded `type_mapping` in
  `backend/routes/garmin.py` ~214. Expose via `GET /api/garmin/benchmarks`,
  `get_garmin_health`, and the plan prompt — use threshold pace/HR to state prescriptions as
  numbers instead of vague terms.
- Handoff: T-030 (PT-panel benchmarks card) is blocked on `GET /api/garmin/benchmarks`.

### [T-021] Adherence silently reports 0% when Strava sync is broken or stale
- Owner: claude
- Status: todo
- Priority: P1
- Created-by: anders (GitHub issue #187)
- Files: `backend/routes/trainer.py` (`compute_adherence` ~480, `trainer_daily_checkin` ~2437), `backend/services/sync_status.py`, `tests/test_trainer_routes.py`
- Spec: `compute_adherence()` looks only at `strava_activities`; a broken/stale/expired
  Strava sync silently produces "0 of 5 completed (0.0%)" as a stated fact in the check-in
  prompt, and the coach scales the plan down for a user training normally. Union Garmin
  completion dates in (`garmin_health` where `workout_duration > 0` until T-011 lands, then
  `garmin_activities`), mirroring the existing double-count rule in
  `build_training_load_summary()`. Add `reliable` + `reason` to the return value —
  `adherence_pct = None` when no source can be trusted for the window, read from
  `get_sync_states()`. Stop swallowing the query exception into a silent zero. Surface
  unreliability in the check-in prompt text.
- Handoff: T-031 (PT-panel warning instead of a 0% bar) is blocked on the `reliable`/`reason`
  fields landing on `GET /api/trainer/adherence`.

### [T-022] Make Garmin the primary activity source, Strava the completeness net
- Owner: claude
- Status: todo
- Priority: P2
- Created-by: anders (GitHub issue #188)
- Files: `backend/routes/trainer.py` (`build_training_load_summary` ~531, `compute_adherence` ~480, `_collect_onboarding_signals` ~1800, `trainer_daily_checkin` ~2394, plan-generation prompt ~1513), new service module
- Depends-on: T-011 (`garmin_activities` table)
- Spec: `build_training_load_summary()`'s docstring has the hierarchy backwards — sessions
  are recorded on the watch and pushed *to* Strava, so Garmin is the original and Strava the
  copy. New shared `unified_sessions(start, end)` helper: Garmin-first merge, matching
  Strava activities with no Garmin counterpart on start-time (±10 min) + duration (±10%),
  not date alone (date-only matching drops genuine same-day second sessions). Each session
  gets a `source` field. Migrate all five call sites listed in the issue onto this one
  helper instead of five independently-drifting decisions; log near-misses during rollout to
  tune tolerances. Correct the docstring. Tests: same session in both sources counts once
  with Garmin's richer fields kept; Strava-only activity included; two genuine same-day
  sessions both survive; outage in either source falls back to the other.

### [T-023] Decide what new Garmin data adds to prompts vs stays tool-only, and budget it
- Owner: claude
- Status: todo
- Priority: P2
- Created-by: anders (GitHub issue #189)
- Files: `backend/routes/trainer.py` (`build_chat_context_block` ~1263, plan prompt ~1513, onboarding ~1969, `trainer_daily_checkin` ~2335), `backend/services/tool_registry.py`
- Spec: Coordination issue, not a feature — decide this **before** the first of T-013/T-014/
  T-016/T-018/T-019/T-020 lands its prompt-injection piece, then land the assignment
  alongside that first one. Three tiers: **A — always resident** (readiness score+level,
  ACWR+status, training status, last night's sleep/HRV vs baseline, today's planned
  session). **B — resident only when it deviates** (weekly easy/hard split, load balance vs
  targets, load-trend direction — apply the existing `recompute_health_baselines()` bands).
  **C — tool-call only** (laps, per-session zones, historical benchmarks, set-by-set
  strength — register a tool for each; #185's laps have none today). Budget:
  `build_chat_context_block()` should stay under ~800 tokens — enforce with a test against a
  fully-populated fixture, not review. Plan-generation prompt gets its own, larger, measured
  budget. Also fold in T-022's merged `unified_sessions()` so the plan prompt stops emitting
  separate Garmin/Strava blocks for the same session.

### [T-010] Push planned workouts from F.R.E.J.A. to the Garmin watch
- Owner: claude
- Status: todo
- Priority: P2
- Created-by: anders (GitHub issue #176)
- Files: `backend/services/garmin_workout.py` (new), `backend/routes/garmin.py`, `backend/services/plan_export.py` (`plan_occurrences`), `backend/routes/trainer/generation.py`
- Depends-on: T-015 (auth robustness — this writes to the account, a half-authenticated
  write is worse than a failed read) is a soft prerequisite, not a hard block; can start
  step 1 in parallel.
- Spec: **Step 1 (land this):** build Garmin workout JSON from a plan session
  (`activity_type` → sport type, `duration_minutes` → step target) via
  `upload_workout`/`schedule_workout`; `POST /api/garmin/workouts/push` (plan id + start
  date) runs `plan_occurrences()` and uploads+schedules each session; migration/table for
  the returned `workoutId`/`scheduleId` per plan session so re-booking doesn't duplicate
  watch entries; wire the daily check-in's `adjust_workout` path to update/reschedule the
  already-pushed workout instead of adding a second one; `DELETE` path to
  unschedule/remove, with confirmation (writes to the real account). **Steps 2-3 (later,
  separate PRs):** richer step structure with HR/pace zones; strength sessions at exercise
  level via the same name table as T-017.
- Handoff: T-024 ("skicka till klockan" button next to plan export) is blocked on
  `POST /api/garmin/workouts/push` existing.

---

### [T-024] Client: "skicka till klockan" action next to the plan export
- Owner: antigravity
- Status: blocked
- Priority: P2
- Created-by: claude (split from GitHub issue #176)
- Files: `client/**` (wherever the plan export action lives)
- Blocked by: T-010 (`POST /api/garmin/workouts/push` must exist first)
- Handoff-notes: New endpoint takes a plan id + start date and pushes/schedules every
  session in the plan to the user's Garmin watch. It writes to a real external account, so
  the button must confirm before firing, and should show per-session success/failure (a
  partial push is likely — some sport types aren't supported on every watch model).
- ▶ Antigravity prompt: "Add a 'Skicka till klockan' button next to the existing plan-export
  action in the client. On click, confirm with the user first (this writes real workouts to
  their Garmin account), then call `POST /api/garmin/workouts/push` with the plan id and
  start date. Show a per-session result (pushed / failed, with the reason) rather than one
  pass/fail for the whole plan. Browser-verify the confirm step and both the all-success and
  partial-failure states against the running backend, screenshot each, commit and push."

### [T-025] Client: HUD readiness card (Garmin Training Readiness)
- Owner: antigravity
- Status: todo — UNBLOCKED 2026-07-24, ready for Antigravity to run
- Priority: P2
- Created-by: claude (split from GitHub issue #180)
- Files: `client/js/ui-dashboards.js`, `client/style.css`
- Was blocked by: T-014, now `done` — `training_readiness`/`_level`/`_feedback` are live on
  `GET /api/garmin/data`.
- Handoff-notes: Score is 0-100, `level` is one of `LOW/MODERATE/HIGH/PRIME`, plus a Garmin
  feedback phrase. Should sit alongside the existing body-battery card in the HUD.
- ▶ Antigravity prompt: "Add a Training Readiness card to the HUD dashboard
  (`client/js/ui-dashboards.js`), next to the existing body-battery card. Show the 0-100
  score, a colored badge for `level` (`LOW`/`MODERATE`/`HIGH`/`PRIME`), and Garmin's
  feedback phrase as a subtitle. Handle the field being absent (no card, not a broken one)
  for accounts/days without a reading. Browser-verify against the running backend with a
  populated and an absent case, screenshot both, commit and push."

### [T-026] Client: Garmin re-auth UI (settings panel)
- Owner: antigravity
- Status: todo — UNBLOCKED 2026-07-24, ready for Antigravity to run (MFA step dropped, see below)
- Priority: P2
- Created-by: claude (split from GitHub issue #181)
- Files: `client/**` (settings panel / sync-status indicator)
- Was blocked by: T-015, now `done` — `sync_status` distinguishes `auth_required` /
  `rate_limited` / `error`; `POST /api/garmin/reauth` and `GET /api/garmin/credentials`'
  `token_age_days`/`token_stale_warning` are live.
- Handoff-notes: T-015 deliberately did **not** build MFA (needs confirming 2FA is even
  enabled on this account, plus a stateful two-call design — see T-015's board note). There
  is no "MFA code required" response to handle — drop that step from scope. A 2FA-enabled
  account's reauth attempt will currently just fail with `auth_required`, same as any other
  auth failure.
- ▶ Antigravity prompt: "In the settings panel and the sync-status indicator, when Garmin's
  sync state is `auth_required` show 'Garmin-inloggningen har gått ut — logga in igen' with
  a button that calls `POST /api/garmin/reauth`. When the state is `rate_limited` show a
  distinct message like 'Garmin begränsar just nu — försök igen om en stund' (not the same
  text as auth_required). Keep the existing generic error path for a plain `error` state.
  Also show a token-age warning banner when `GET /api/garmin/credentials` returns
  `token_stale_warning: true`. There is no MFA flow to build - the backend doesn't support
  it yet, so don't add a code-input step. Browser-verify each state against the running
  backend, screenshot, commit and push."

### [T-027] Client: PT-panel strength-log source badge (manual vs Garmin)
- Owner: antigravity
- Status: todo — UNBLOCKED 2026-07-24, ready for Antigravity to run
- Priority: P2
- Created-by: claude (split from GitHub issue #183)
- Files: `client/**` (PT strength-log panel)
- Was blocked by: T-017, now `done` — `source` (`"manual"`/`"garmin"`) is live on
  `GET /api/trainer/strength/log`.
- Handoff-notes: Rows now carry `source: "manual" | "garmin"`. Users should be able to tell
  which rows were auto-imported from the watch vs typed by hand.
- ▶ Antigravity prompt: "In the PT strength-log panel, add a small badge/icon on each row
  showing whether it came from `source: manual` or `source: garmin`. Keep it unobtrusive —
  a watch icon or similar next to Garmin-sourced rows is enough. Browser-verify against the
  running backend with a mix of both sources, screenshot, commit and push."

### [T-028] Client: HUD stacked weekly HR-zone bar
- Owner: antigravity
- Status: blocked
- Priority: P2
- Created-by: claude (split from GitHub issue #184)
- Files: `client/js/ui-dashboards.js`
- Blocked by: T-018 (`GET /api/garmin/zones?days=N`)
- Handoff-notes: Endpoint returns per-session seconds in each of 5 HR zones. A stacked bar
  per week (or per session) showing the zone-1..5 split is the deliverable; this is the
  first intensity-aware chart in the HUD.
- ▶ Antigravity prompt: "Add a stacked weekly HR-zone bar chart to the HUD dashboard
  (`client/js/ui-dashboards.js`), fed by `GET /api/garmin/zones?days=N`. One stacked bar per
  week, segments for zones 1-5 (low to high, consistent color ramp). Handle weeks with no
  zone data (omit the bar, don't render an empty one). Browser-verify against the running
  backend, screenshot, commit and push."

### [T-029] Client: PT-panel lap table
- Owner: antigravity
- Status: blocked
- Priority: P2
- Created-by: claude (split from GitHub issue #185)
- Files: `client/**` (PT panel, activity detail view)
- Blocked by: T-019 step 1 (`GET /api/garmin/activities/{id}/laps`)
- Handoff-notes: Per-lap distance, duration, pace (derive from speed), HR, and
  `intensity_type` (`ACTIVE`/`REST`/`WARMUP`/`COOLDOWN`). Useful stand-alone before any
  adherence-grading logic exists.
- ▶ Antigravity prompt: "Add a lap table to the PT-panel activity detail view, fed by `GET
  /api/garmin/activities/{id}/laps`. Columns: lap #, distance, duration, pace (derived from
  speed), avg HR, intensity type. Only show the table for activities that have laps (hide it
  otherwise, don't show an empty table). Browser-verify against the running backend with a
  multi-lap activity, screenshot, commit and push."

### [T-030] Client: PT-panel Garmin benchmarks card
- Owner: antigravity
- Status: blocked
- Priority: P3
- Created-by: claude (split from GitHub issue #186)
- Files: `client/**` (PT panel)
- Blocked by: T-020 (`GET /api/garmin/benchmarks`)
- Handoff-notes: Key/value benchmarks (threshold pace/HR, race predictions, PRs, endurance/
  hill score, fitness age). Not every user will have every benchmark — render only the ones
  present.
- ▶ Antigravity prompt: "Add a benchmarks card to the PT panel, fed by `GET
  /api/garmin/benchmarks`. Render only the key/value pairs the account actually has (skip
  absent ones silently, no placeholder for missing data). Group loosely: pace/HR
  (threshold, race predictions, PRs) vs trend scores (endurance, hill, fitness age).
  Browser-verify against the running backend, screenshot, commit and push."

### [T-031] Client: PT-panel adherence warning instead of a 0% bar
- Owner: antigravity
- Status: blocked
- Priority: P1
- Created-by: claude (split from GitHub issue #187)
- Files: `client/**` (PT panel adherence widget)
- Blocked by: T-021 (`reliable` + `reason` fields on `GET /api/trainer/adherence`)
- Handoff-notes: Today a broken Strava sync renders as a 0% adherence bar — indistinguishable
  from "the user actually skipped every session". After T-021 the response carries
  `reliable: bool` and a `reason` string when it's `false`.
- ▶ Antigravity prompt: "In the PT-panel adherence widget, when `GET
  /api/trainer/adherence` returns `reliable: false`, replace the percentage bar with a
  warning message using the `reason` field (e.g. 'Kan inte avgöra följsamhet just nu — se
  synkstatus') instead of rendering a 0% bar. Keep the normal bar for `reliable: true`.
  Browser-verify both states against the running backend, screenshot, commit and push."

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
