# Full 2GB Telegram Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the original 2GB download-and-delivery behavior without reducing video quality, while keeping Vercel as the public frontend and using the existing Render Docker service for persistent processing and a local Telegram Bot API server.

**Architecture:** Vercel validates public requests and forwards jobs to the existing Render service. The Render container runs the download worker and Telegram Bot API server in `--local` mode in the same filesystem namespace, so completed files up to 2,000,000,000 bytes can be sent by local file URI and deleted immediately afterward. Website jobs upload to the already configured R2 bucket and return short-lived presigned download URLs, so large downloads never pass through Vercel.

**Tech Stack:** FastAPI/Python on Vercel, Node.js worker on Render, yt-dlp, FFmpeg, official TDLib Telegram Bot API server, Cloudflare R2/S3 multipart uploads, Docker, pytest, Node test runner.

## Global Constraints

- Maximum accepted media size is `2_000_000_000` bytes; do not silently reduce quality or select a smaller file solely to fit Vercel.
- Vercel must never download, merge, store, or upload media files.
- Telegram delivery above the cloud Bot API limit must use the local Bot API server with `--local` and a local file URI.
- Temporary files must be deleted in `finally` after delivery or failure.
- One active Telegram media job per chat and one global large-file job on the free Render instance.
- Reject playlists, live streams, private/reserved-network URLs, files above 2GB, and videos longer than 7200 seconds.
- Never log the Telegram bot token, API hash, worker authentication value, cookies, or signed R2 URLs.
- Keep the current Vercel UI/domain and current Telegram bot identity.

---

### Task 1: Restore 2GB configuration and remove the 49MB Vercel path

**Files:**
- Modify: `app/config.py`
- Modify: `app/main.py`
- Modify: `app/vercel_remote.py`
- Delete after migration: `app/vercel_upload.py`
- Test: `tests/test_vercel_remote.py`
- Test: `tests/test_vercel_upload.py`

**Interfaces:**
- Produces: `settings.remote_worker_url: str`, `settings.telegram_send_limit_bytes == 2_000_000_000`.
- Produces: `forward_telegram_update(data: dict) -> dict` that sends metadata only to Render.

- [ ] **Step 1: Write failing configuration tests**

```python
def test_telegram_limit_remains_two_gigabytes():
    from app.config import settings
    assert settings.telegram_send_limit_bytes == 2_000_000_000


def test_vercel_forwarder_does_not_import_downloader():
    import app.vercel_remote as module
    assert not hasattr(module, "download_and_upload")
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/test_vercel_remote.py tests/test_vercel_upload.py -v`
Expected: the current 49MB assertion/import path fails.

- [ ] **Step 3: Implement metadata-only forwarding**

Set `telegram_send_limit_bytes` to `2_000_000_000`, add `remote_worker_url` and a bounded `httpx` forwarder. The forwarded payload contains only Telegram update JSON; no file bytes are created under `/tmp`.

- [ ] **Step 4: Remove the direct Vercel downloader/uploader**

Delete `app/vercel_upload.py` and update imports so Vercel no longer imports yt-dlp for Telegram delivery.

- [ ] **Step 5: Run tests**

Run: `pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app tests
git commit -m "fix: restore 2gb remote Telegram delivery"
```

### Task 2: Make the Render worker accept standalone Telegram jobs

**Files:**
- Modify in `yjlvfe/YJVideoDownloads`: `worker/worker.mjs`
- Create in `yjlvfe/YJVideoDownloads`: `worker/job-store.mjs`
- Create in `yjlvfe/YJVideoDownloads`: `worker/telegram-jobs.mjs`
- Test in `yjlvfe/YJVideoDownloads`: `worker/telegram-jobs.test.mjs`

**Interfaces:**
- Consumes: `POST /telegram/update` with `x-telegram-worker-auth`.
- Produces: HTTP `202` immediately and `enqueueTelegramUpdate(update) -> {accepted, duplicate}`.
- Produces: one FIFO worker with duplicate-update protection and one active job per chat.

- [ ] **Step 1: Write failing tests for URL extraction and dedupe**

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { extractTelegramJob, UpdateDedupe } from "./telegram-jobs.mjs";

test("extracts a URL job from a private message", () => {
  const job = extractTelegramJob({update_id: 7, message: {text: "https://example.com/v", chat: {id: 9}, from: {id: 9}}});
  assert.equal(job.sourceUrl, "https://example.com/v");
  assert.equal(job.chatId, 9);
});

test("rejects a duplicate update", () => {
  const dedupe = new UpdateDedupe(512);
  assert.equal(dedupe.remember(7), true);
  assert.equal(dedupe.remember(7), false);
});
```

- [ ] **Step 2: Run tests and verify failure**

Run: `node --test worker/telegram-jobs.test.mjs`
Expected: module-not-found failure.

- [ ] **Step 3: Implement queue and authentication**

Implement bounded in-memory FIFO, duplicate update IDs, per-chat busy response, URL validation, Arabic progress messages, and `202` response before download starts.

- [ ] **Step 4: Preserve the existing R2 queue mode as optional**

Do not require `APP_BASE_URL` or R2 variables when only Telegram mode is configured. Existing claim/wake behavior remains enabled when all R2/app variables exist.

- [ ] **Step 5: Run tests**

Run: `node --test worker/*.test.mjs`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add worker
git commit -m "feat: accept standalone Telegram download jobs"
```

### Task 3: Bundle the local Telegram Bot API server in the Render image

**Files:**
- Modify in `yjlvfe/YJVideoDownloads`: `Dockerfile`
- Create in `yjlvfe/YJVideoDownloads`: `docker/start.sh`
- Modify in `yjlvfe/YJVideoDownloads`: `worker/worker.mjs`
- Test in `yjlvfe/YJVideoDownloads`: `worker/telegram-local.test.mjs`

**Interfaces:**
- Produces: local server at `http://127.0.0.1:8081`.
- Produces: `sendLocalTelegramFile(job, path, name, size)` for files up to `2_000_000_000` bytes.
- Produces: public health response with booleans and free disk bytes only.

- [ ] **Step 1: Add a failing file-limit test**

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { canSendLocally } from "./telegram-jobs.mjs";

test("accepts exactly the configured 2GB ceiling", () => {
  assert.equal(canSendLocally(2_000_000_000), true);
  assert.equal(canSendLocally(2_000_000_001), false);
});
```

- [ ] **Step 2: Build a single-container image**

Use `aiogram/telegram-bot-api:10.2` as a binary stage, copy `telegram-bot-api` into the Node image, install Alpine runtime libraries, Node dependencies, Python, yt-dlp, and FFmpeg. Copy both `worker.mjs` and `ytdlp-options.mjs`.

- [ ] **Step 3: Add deterministic startup**

`docker/start.sh` creates writable Telegram/temp directories, starts `telegram-bot-api` with `--local --http-port=8081`, waits for health, then `exec node worker.mjs`. It exits if API ID/hash are missing or the local server dies.

- [ ] **Step 4: Send local files by absolute path**

Use an absolute path accepted by local Bot API, send as video first and document second, and never upload bytes through Node HTTP multipart.

- [ ] **Step 5: Build and test locally**

Run: `docker build -t yj-video-worker .`
Expected: successful image build.

Run: `node --test worker/*.test.mjs`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker worker
git commit -m "feat: run local Telegram Bot API beside worker"
```

### Task 4: Add large website jobs and R2 presigned downloads

**Files:**
- Modify in `yjlvfe/YJVideoDownloads`: `worker/worker.mjs`
- Create in `yjlvfe/YJVideoDownloads`: `worker/web-jobs.mjs`
- Modify in `yjlvfe/YJVideoDownloads`: `worker/package.json`
- Test in `yjlvfe/YJVideoDownloads`: `worker/web-jobs.test.mjs`

**Interfaces:**
- Produces: `POST /api/inspect`, `POST /api/jobs`, `GET /api/jobs/:id`.
- Produces: completed job JSON containing a short-lived R2 presigned URL.

- [ ] **Step 1: Write state-transition tests**

```javascript
test("job progresses from queued to ready", async () => {
  const store = new WebJobStore();
  const job = store.create({sourceUrl: "https://example.com/v"});
  store.progress(job.id, "downloading", 40);
  store.complete(job.id, "https://signed.example/file");
  assert.equal(store.get(job.id).status, "ready");
});
```

- [ ] **Step 2: Implement endpoints with rate limits**

Limit one large active job per IP, validate URLs against private/reserved networks, and cap size/duration at the global constraints.

- [ ] **Step 3: Generate signed R2 URLs**

Add `@aws-sdk/s3-request-presigner`; return a 15-minute `GetObject` URL after multipart upload. Never log the signed URL.

- [ ] **Step 4: Run tests**

Run: `node --test worker/*.test.mjs`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add worker
git commit -m "feat: expose large download jobs through R2"
```

### Task 5: Wire the Vercel website and webhook to Render

**Files:**
- Modify: `app/main.py`
- Modify: `app/static/app.js`
- Create: `app/remote_worker.py`
- Test: `tests/test_remote_worker.py`

**Interfaces:**
- Consumes: Render worker endpoints from Tasks 2 and 4.
- Produces: existing Vercel routes with unchanged browser-facing response shapes.

- [ ] **Step 1: Write proxy-contract tests**

Mock Render responses and assert `/api/inspect`, `/api/jobs`, and `/api/jobs/{id}` preserve status codes, safe error messages, and ready download URLs.

- [ ] **Step 2: Implement bounded proxy calls**

Use connect/read timeouts, no retries for non-idempotent job creation, one retry for status/inspect, and redact upstream response bodies on 5xx.

- [ ] **Step 3: Forward Telegram URL updates**

The webhook validates Telegram's secret header, forwards the update to Render, and returns `200` for accepted or duplicate updates. No yt-dlp or media upload executes on Vercel.

- [ ] **Step 4: Update the browser flow**

Continue showing elapsed time and progress. When a ready job contains a signed URL, navigate directly to it instead of requesting a Vercel `/download` route.

- [ ] **Step 5: Run tests and static checks**

Run: `pytest -q`
Expected: pass.

Run: `node --check app/static/app.js`
Expected: no output and exit code 0.

- [ ] **Step 6: Commit**

```bash
git add app tests
git commit -m "feat: route large downloads through Render worker"
```

### Task 6: Deploy, migrate the bot, and verify production

**Files:**
- Modify in `yjlvfe/YJVideoDownloads`: `README.md`
- Modify: `README.md`

**Interfaces:**
- Render health: `/health` reports local Bot API ready, free disk bytes, queue depth, and R2 configured booleans.
- Vercel health: `/api/health` reports remote worker connectivity without exposing secrets.

- [ ] **Step 1: Deploy Render from the existing Docker service**

Push the Render repository branch and wait for `live`. Verify Docker starts both processes and binds `$PORT`.

- [ ] **Step 2: Configure secrets without logging them**

Set Telegram API ID/hash/token, R2 credentials, and worker authentication in Render environment variables. Keep existing values when present.

- [ ] **Step 3: Move the bot to the local server**

Call cloud `logOut`, then local `setWebhook` pointing to `https://yj-download-video.vercel.app/webhook/telegram` with the existing secret token. Confirm `getWebhookInfo` reports zero pending updates and no last error.

- [ ] **Step 4: Deploy Vercel**

Push the Vercel repository branch, wait for `READY`, verify `/api/health`, `/api/telegram/status`, static assets, and Render connectivity.

- [ ] **Step 5: End-to-end verification**

Verify:

1. A small TikTok video is delivered.
2. A media file larger than 50MB is delivered through local Bot API.
3. A file above 2GB is rejected before download.
4. The website returns a signed R2 download URL for a large job.
5. Temporary directories are removed after success and failure.
6. Duplicate Telegram webhook updates create only one job.
7. Logs contain no bot token, API hash, cookies, or signed download URL.

- [ ] **Step 6: Document operational limits**

Document that Render Free can sleep/restart and has ephemeral local storage, while completed files are immediately sent or stored in R2 and temporary local files are disposable.

- [ ] **Step 7: Commit**

```bash
git add README.md
git commit -m "docs: document 2gb production architecture"
```
