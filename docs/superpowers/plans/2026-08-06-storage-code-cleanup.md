# Storage Safety and Code Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make storage cleanup safe for long-running downloads, shrink the production image, replace obsolete tests with a focused regression suite, and verify the exact production commit before merging to `main`.

**Architecture:** Introduce an on-disk active-download lease registry under the existing storage root, make every web and Telegram media job acquire and refresh a lease, and make cleanup protect leased paths while removing expired or orphaned data. Separate runtime and development dependencies, use a multi-stage Docker build, and keep Vercel stateless while Render remains the only stateful worker.

**Tech Stack:** Python 3.12, FastAPI, pytest, SQLite, yt-dlp, FFmpeg/ffprobe, Docker, GitHub Actions, Render, Vercel.

## Global Constraints

- Never delete, rotate, replace, print, or expose API keys, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_BOT_TOKEN`, cookies, proxy credentials, worker secrets, or admin secrets.
- Do not merge to `main` until the full test suite, compile step, dependency audit, secret scan, and Docker build succeed for the branch head.
- Do not delete a storage path that has a live lease.
- Telegram temporary media must be removed immediately in `finally` after send success or failure.
- Web media must remain downloadable until first successful response completion or TTL expiry, then become unavailable.
- Vercel remains a stateless proxy; Render remains the stateful runtime.

---

### Task 1: Active storage leases

**Files:**
- Create: `app/storage_leases.py`
- Modify: `app/storage_lifecycle.py`
- Test: `tests/test_storage_safety.py`

**Interfaces:**
- Produces: `StorageLeaseRegistry(root: Path, stale_after_seconds: int)`, `lease(path: Path)`, `active_paths(now: float) -> set[Path]`, `clear_stale(now: float) -> int`.
- Cleanup consumes the registry result through the existing `active_paths` argument.

- [ ] Write failing tests proving an expired entry is retained while leased, stale leases are ignored, and paths outside storage cannot be registered.
- [ ] Run `pytest tests/test_storage_safety.py -q` and confirm failure because the registry does not exist.
- [ ] Implement atomic JSON lease files with containment checks and heartbeat updates.
- [ ] Run the focused tests and then all storage tests.
- [ ] Commit.

### Task 2: Protect web and Telegram jobs

**Files:**
- Modify: `app/main.py`
- Modify: `app/worker.py`
- Modify: `app/telegram_flow.py`
- Modify: `app/durable_runtime.py`
- Test: `tests/test_storage_safety.py`
- Test: `tests/test_storage_job_cleanup.py`

**Interfaces:**
- Every job creates a lease before writing media, refreshes it during progress, and releases it in `finally`.
- The cleanup loop obtains active paths from the registry instead of passing an empty set.

- [ ] Write failing tests for active web job protection, Telegram immediate cleanup, and lease release after exceptions.
- [ ] Run the focused tests and confirm the intended failures.
- [ ] Wire leases into web extraction and Telegram processing.
- [ ] Update startup and periodic cleanup to protect live leases and clear stale lease metadata.
- [ ] Run focused and full tests.
- [ ] Commit.

### Task 3: Expire database references with deleted files

**Files:**
- Create: `app/job_expiration.py`
- Modify: `app/main.py`
- Modify: `app/db.py`
- Test: `tests/test_job_expiration.py`

**Interfaces:**
- Produces: `expire_missing_and_elapsed_jobs(now) -> ExpirationResult`.
- Clears `file_path` and `download_token` and marks nonterminal ready jobs `expired` when the media file is gone or expiry elapsed.

- [ ] Write failing tests for elapsed expiry, missing files, and idempotency.
- [ ] Run tests and confirm failure.
- [ ] Implement database reconciliation after each cleanup cycle and on startup.
- [ ] Run tests and commit.

### Task 4: Replace obsolete storage tests

**Files:**
- Delete only superseded storage/cleanup tests after their behavior is covered by the new suite.
- Keep extraction, Telegram API, security, queue, quality, and YouTube policy tests unless proven duplicate.
- Create: `tests/test_storage_contract.py`.

- [ ] Build one contract suite covering containment, active leases, orphan cleanup, immediate Telegram cleanup, TTL cleanup, database reconciliation, and symlink safety.
- [ ] Run the old and new suites together.
- [ ] Remove only exact duplicates and rerun the full suite.
- [ ] Commit.

### Task 5: Production dependency and Docker split

**Files:**
- Create: `requirements-runtime.txt`
- Create: `requirements-dev.txt`
- Replace: `requirements.txt` with compatibility include files.
- Create: `.dockerignore`
- Modify: `Dockerfile`
- Test: `tests/test_packaging_contract.py`

- [ ] Write failing packaging tests proving pytest is absent from runtime dependencies, test files are excluded from the final image context, and the runtime stage is non-root where supported.
- [ ] Run packaging tests and confirm failure.
- [ ] Split dependencies and convert Dockerfile to builder/runtime stages while preserving Telegram Bot API, FFmpeg, Chromium, Node, PO-token provider, cookies, and proxy support.
- [ ] Run compile, tests, and Docker build.
- [ ] Commit.

### Task 6: CI release gate on `main`

**Files:**
- Modify: `.github/workflows/tests.yml`
- Create: `scripts/check_tracked_secrets.py`
- Test: `tests/test_ci_contract.py`

- [ ] Write failing tests asserting CI triggers on `pull_request`, `fix/**`, and `main`, and includes compile, full tests, dependency audit, tracked-secret scan, and Docker build.
- [ ] Run focused tests and confirm failure.
- [ ] Implement the release gate without printing secret values.
- [ ] Run focused tests and commit.

### Task 7: Remove unnecessary runtime patch layers conservatively

**Files:**
- Modify: `app/extractor.py`
- Modify: `app/telegram_flow.py`
- Modify: `app/platform_runtime.py`
- Delete: `app/quality_runtime_patch.py` only after behavior is moved and tests prove equivalence.
- Delete: `app/telegram_quality_runtime_patch.py` only after behavior is moved and tests prove equivalence.
- Test: quality, inspection, Telegram flow, YouTube runtime, and video delivery suites.

- [ ] Add characterization tests for the current quality and fallback behavior.
- [ ] Move quality inspection and safe fallback into the owning classes without changing outputs.
- [ ] Remove the corresponding monkey patches.
- [ ] Run the full suite and compare behavior.
- [ ] Commit.

### Task 8: Final verification and integration

- [ ] Run the complete GitHub Actions gate on the branch head.
- [ ] Build the production Docker image through the same Dockerfile used by Render.
- [ ] Open a PR, review the complete diff, and merge it without requiring user action.
- [ ] Verify Render deploys the merged `main` SHA and `/health` is ready.
- [ ] Verify Vercel Production is READY on the same `main` SHA and has no new runtime errors.
- [ ] Confirm no secret values changed and report any platform limitation honestly.
