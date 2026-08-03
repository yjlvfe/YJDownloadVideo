# YJDownloadVideo Production Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver one durable, secure Vercel-to-Render media pipeline with deterministic quality selectors, bounded YouTube failure handling, and verified TikTok/Instagram/YouTube behavior.

**Architecture:** Vercel remains a thin signed ingress. Render durably accepts jobs and performs extraction/upload. Media selection is centralized in a registry; YouTube rate-limit/bot-check failures open a durable circuit rather than triggering alternate-client storms. Startup never mutates dependencies or deletes active storage.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/SQLite, optional Redis, yt-dlp, FFmpeg, Telegram Local Bot API, Docker, pytest, GitHub Actions, Vercel, Render.

## Global Constraints

- Never delete, replace, rotate, print, or commit `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_BOT_TOKEN`, API keys, cookie secrets, or existing environment variables.
- Use merge semantics for environment updates.
- Work only on `fix/production-recovery-20260804` until release approval.
- Pin the runtime yt-dlp version and never upgrade it during container startup.
- Do not promote unless Vercel and Render report the same Git SHA.
- The acceptance URLs are:
  - `https://vm.tiktok.com/ZGdxjnte8/`
  - `https://www.instagram.com/reel/DbkuNu8oMA5/?igsh=MWVuMjJlOWszdHQzMw==`
  - `https://youtu.be/kTRs4ycFp_o?si=1T_A4IsWT8fxnkce`
  - `https://youtube.com/shorts/T7DA7VTZ_Zs?si=1F9UnPcRym93-Tyu`

---

### Task 1: Deterministic dependency and runtime configuration

**Files:**
- Modify: `requirements.txt`
- Modify: `Dockerfile`
- Modify: `docker/start-render.sh`
- Modify: `app/config.py`
- Test: `tests/test_runtime_contract.py`

**Interfaces:**
- Produces: `settings.git_sha`, `settings.worker_shared_secret`, `settings.youtube_proxy_url`, `settings.enable_public_youtube_fallback`, `settings.file_ttl_seconds`.

- [ ] **Step 1: Write failing runtime-contract tests**

Create tests asserting that requirements pins `yt-dlp==2026.7.23.234303.dev0`, startup does not run `pip install`, production rejects `SECRET_KEY=change-me`, and health can read `GIT_SHA` without exposing secrets.

- [ ] **Step 2: Run the targeted tests**

Run: `PYTHONPATH=. pytest -q tests/test_runtime_contract.py`
Expected: FAIL because yt-dlp is not pinned, startup upgrades it, and settings lack the new fields.

- [ ] **Step 3: Implement the runtime contract**

Pin yt-dlp, remove startup upgrades, add the settings fields, default `FILE_TTL_SECONDS` to `3600`, and add a production-safety validator that never logs secret values.

- [ ] **Step 4: Verify targeted and complete tests**

Run: `PYTHONPATH=. pytest -q tests/test_runtime_contract.py && PYTHONPATH=. pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `fix: make media runtime deterministic`.

---

### Task 2: Central quality registry and truthful quality buttons

**Files:**
- Modify: `app/media_choice.py`
- Modify: `app/telegram_flow.py`
- Modify: `app/main.py`
- Modify: `app/extractor.py`
- Test: `tests/test_quality_registry.py`
- Test: `tests/test_telegram_flow.py`

**Interfaces:**
- Produces: `quality_options() -> list[QualityOption]`, `selector_for_quality(key: str) -> str`, `quality_key_for_selector(selector: str) -> str | None`.

- [ ] **Step 1: Write failing registry tests**

Test the exact keys `best`, `1080`, `720`, `480`, and `audio`; verify callback parsing rejects arbitrary selectors; verify each selector contains a combined-file fallback; verify display labels and selectors originate from one registry.

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=. pytest -q tests/test_quality_registry.py tests/test_telegram_flow.py`
Expected: FAIL because presets and selector construction are duplicated.

- [ ] **Step 3: Implement the registry**

Move all quality definitions to `app/media_choice.py`, make Telegram buttons consume the registry, and make web requests validate `format_id` against it. Preserve the existing Arabic button labels.

- [ ] **Step 4: Verify**

Run: `PYTHONPATH=. pytest -q tests/test_quality_registry.py tests/test_telegram_flow.py && PYTHONPATH=. pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

Commit message: `fix: centralize and validate media qualities`.

---

### Task 3: YouTube classifier, bounded retry, and circuit breaker

**Files:**
- Create: `app/youtube_policy.py`
- Modify: `app/extractor.py`
- Modify: `app/platform_runtime.py`
- Test: `tests/test_youtube_policy.py`
- Test: `tests/test_extractor_youtube.py`

**Interfaces:**
- Produces: `classify_youtube_error(exc: BaseException) -> str`, `YouTubeCircuit`, `youtube_retry_allowed(classification: str, attempt: int) -> bool`.

- [ ] **Step 1: Write failing classification and retry tests**

Test classification of HTTP 429, bot-check text, login-required text, signed `googlevideo` 403, unsupported-client warnings, and generic network failures. Test that bot-check/429 permit zero alternate-client retries and a fragment 403 permits exactly one full re-extraction.

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=. pytest -q tests/test_youtube_policy.py tests/test_extractor_youtube.py`
Expected: FAIL because policy and circuit objects do not exist and current code loops across clients.

- [ ] **Step 3: Implement policy and circuit**

Remove `tv_embedded`, remove multi-client loops, classify errors, stop immediately on bot-check/429, allow one same-identity re-extraction for fragment 403, and disable public Invidious/Piped fallback unless `ENABLE_PUBLIC_YOUTUBE_FALLBACK=1`.

- [ ] **Step 4: Verify no generic googlevideo fallback**

Run: `PYTHONPATH=. pytest -q tests/test_youtube_policy.py tests/test_extractor_youtube.py`
Expected: PASS with an assertion that a signed `googlevideo` URL is never passed to `_download_candidate`.

- [ ] **Step 5: Run complete suite and commit**

Run: `PYTHONPATH=. pytest -q`
Expected: PASS.

Commit message: `fix: bound youtube retries and add circuit policy`.

---

### Task 4: Durable job acceptance and idempotency

**Files:**
- Create: `app/job_queue.py`
- Modify: `app/db.py`
- Modify: `app/main.py`
- Modify: `app/telegram_flow.py`
- Test: `tests/test_job_queue.py`
- Test: `tests/test_render_bridge.py`

**Interfaces:**
- Produces: `JobQueue.accept_update(update_id, payload)`, `JobQueue.claim(worker_id, lease_seconds)`, `JobQueue.heartbeat(job_id, worker_id)`, `JobQueue.complete(job_id)`, `JobQueue.fail(job_id, code, retry_at)`.

- [ ] **Step 1: Write failing durability tests**

Test duplicate `update_id`, process recreation using the same SQLite database, expired lease recovery, terminal-state immutability, and acceptance before HTTP `202`.

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=. pytest -q tests/test_job_queue.py tests/test_render_bridge.py`
Expected: FAIL because the queue is `asyncio.Queue` and updates are accepted only in memory.

- [ ] **Step 3: Implement SQLite-backed durable queue with optional Redis adapter**

Use SQLite as the required single-instance baseline. Preserve `REDIS_URL` for a later adapter without making Redis mandatory. Store payload JSON, update ID, state, attempt, lease owner, lease expiry, heartbeat, safe error code, and timestamps.

- [ ] **Step 4: Make worker consume durable jobs**

Change the Render endpoint to durably accept and return `202`; start one worker loop that claims jobs and invokes TelegramFlow processing. A restart must recover expired claims.

- [ ] **Step 5: Verify and commit**

Run: `PYTHONPATH=. pytest -q tests/test_job_queue.py tests/test_render_bridge.py && PYTHONPATH=. pytest -q`
Expected: PASS.

Commit message: `fix: persist telegram jobs and deduplicate updates`.

---

### Task 5: Timestamped worker authentication and protected webhook repair

**Files:**
- Modify: `app/remote_worker.py`
- Modify: `app/main.py`
- Modify: `app/config.py`
- Test: `tests/test_worker_auth.py`
- Test: `tests/test_render_bridge.py`

**Interfaces:**
- Produces: versioned `create_worker_signature(secret, body, context, timestamp, nonce)` and `verify_worker_request(...)`.

- [ ] **Step 1: Write failing security tests**

Test valid signature, tampered body, wrong context, request older than 300 seconds, future timestamp beyond skew, reused nonce, and legacy migration behavior. Test that webhook repair rejects GET and unauthenticated POST.

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=. pytest -q tests/test_worker_auth.py tests/test_render_bridge.py`
Expected: FAIL because signatures contain no timestamp/nonce and repair is public GET behavior.

- [ ] **Step 3: Implement dedicated-secret authentication**

Use `WORKER_SHARED_SECRET` when set; support legacy token verification only while health reports `workerAuthMode=legacy`. Include timestamp and nonce headers and store nonce consumption in the durable queue database.

- [ ] **Step 4: Protect repair endpoint**

Expose redacted GET status and an authenticated POST repair route requiring `ADMIN_API_TOKEN`; never return or log secret values.

- [ ] **Step 5: Verify and commit**

Run: `PYTHONPATH=. pytest -q tests/test_worker_auth.py tests/test_render_bridge.py && PYTHONPATH=. pytest -q`
Expected: PASS.

Commit message: `fix: prevent replay and protect telegram repair`.

---

### Task 6: Safe storage lifecycle and health contract

**Files:**
- Create: `app/storage_lifecycle.py`
- Modify: `app/telegram_flow.py`
- Modify: `docker/start-render.sh`
- Modify: `app/main.py`
- Test: `tests/test_storage_lifecycle.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Produces: `cleanup_expired_storage(root, now, ttl_seconds, active_paths) -> CleanupResult`.

- [ ] **Step 1: Write failing storage tests**

Test that startup preserves active and recent job directories, terminal expired files are removed, cleanup never traverses outside storage, successful Telegram delivery removes its job directory, and `/health` makes no authenticated Telegram request.

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=. pytest -q tests/test_storage_lifecycle.py tests/test_health.py`
Expected: FAIL because startup currently removes `/app/storage/*` and TelegramFlow clears storage.

- [ ] **Step 3: Implement safe cleanup and health**

Remove unconditional wipes. Add bounded cleanup. Return safe fields: `gitSha`, `ytDlpVersion`, `queueDepth`, `activeJobs`, `youtubeCircuitState`, cookie-configuration booleans, FFmpeg/JS readiness, retained bytes, and free bytes.

- [ ] **Step 4: Verify and commit**

Run: `PYTHONPATH=. pytest -q tests/test_storage_lifecycle.py tests/test_health.py && PYTHONPATH=. pytest -q`
Expected: PASS.

Commit message: `fix: preserve active media and expose safe health`.

---

### Task 7: SSRF and rate-limit hardening

**Files:**
- Modify: `app/security.py`
- Modify: `app/main.py`
- Test: `tests/test_security.py`
- Test: `tests/test_rate_limits.py`

**Interfaces:**
- Produces: `validate_public_url`, `validate_redirect_chain`, and route-specific rate-limit keys.

- [ ] **Step 1: Write failing tests**

Test credentials in URLs, IPv4 and IPv6 private ranges, DNS failure, private redirect targets, mixed public/private DNS answers, and per-user Telegram/web limits.

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=. pytest -q tests/test_security.py tests/test_rate_limits.py`
Expected: FAIL because DNS errors are accepted and redirects are not revalidated.

- [ ] **Step 3: Implement validation and limits**

Fail closed on DNS errors, validate every resolved address and redirect hop, cap redirects, and apply limits to inspect, job creation, worker acceptance, and Telegram enqueue.

- [ ] **Step 4: Verify and commit**

Run: `PYTHONPATH=. pytest -q tests/test_security.py tests/test_rate_limits.py && PYTHONPATH=. pytest -q`
Expected: PASS.

Commit message: `fix: harden public URL validation and limits`.

---

### Task 8: Acceptance harness and synchronized deployment

**Files:**
- Create: `scripts/acceptance_media.py`
- Create: `tests/test_acceptance_manifest.py`
- Modify: `.github/workflows/vercel-media-flow.yml`
- Modify: `README.md`

**Interfaces:**
- Produces: a JSON acceptance report with URL, platform, requested quality, actual height, audio/video presence, file bytes, duration, elapsed time, and classified failure.

- [ ] **Step 1: Write manifest tests**

Test that all four supplied URLs and every registry quality are represented, secrets are never printed, outputs are stored outside Git, and a YouTube circuit-open result is not counted as a successful download.

- [ ] **Step 2: Run tests and confirm RED**

Run: `PYTHONPATH=. pytest -q tests/test_acceptance_manifest.py`
Expected: FAIL because the harness does not exist.

- [ ] **Step 3: Implement harness and CI gate**

Add `--inspect`, `--download`, `--quality`, and `--json` modes. CI runs deterministic unit/integration tests; live media tests run manually or against Render after deployment to avoid external flakiness in every push.

- [ ] **Step 4: Run local deterministic verification**

Run: `PYTHONPATH=. pytest -q && python -m compileall -q app scripts`
Expected: PASS.

- [ ] **Step 5: Deploy preview and run live acceptance**

Deploy the branch to Render/Vercel preview without changing or replacing environment variables. Run the four URLs through available video qualities and audio. Inspect output with ffprobe and save the JSON report.

- [ ] **Step 6: Promote only after synchronized SHA**

Verify `/health` on Vercel and Render reports the branch release SHA, verify webhook status, then merge one PR to `main`. Wait for both production deployments and repeat smoke checks.

- [ ] **Step 7: Commit**

Commit message: `test: add production media acceptance gate`.
