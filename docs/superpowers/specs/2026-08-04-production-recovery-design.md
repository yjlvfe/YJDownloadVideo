# YJDownloadVideo Production Recovery Design

## Goal

Restore a single, testable production architecture for Vercel, Render, Telegram, and media extraction; eliminate retry storms and in-memory job loss; make quality buttons reflect selectors that are actually accepted and verified; and validate the four supplied TikTok, Instagram, YouTube, and YouTube Shorts URLs before promotion.

## Non-negotiable constraints

- Never delete, replace, rotate, print, or commit `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_BOT_TOKEN`, API keys, cookie secrets, or existing environment variables.
- Environment-variable updates must use merge semantics only.
- No direct implementation work on `main`; all changes are made on `fix/production-recovery-20260804`.
- Vercel and Render must expose and report the same Git SHA before production promotion.
- No claim of success without automated tests plus live checks against the four supplied URLs.
- Downloads are only for content the requester is authorized to access; DRM bypass is not supported.

## Architecture

### Vercel ingress

Vercel is a thin FastAPI ingress. It validates Telegram's webhook secret, validates update structure and URLs, derives an idempotency key from `update_id`, signs the exact request body with a dedicated worker secret, forwards it to Render, and returns quickly. It never runs yt-dlp, FFmpeg, Chromium, or long uploads.

### Render worker

Render owns Telegram Local Bot API, extraction, transcoding, upload, health reporting, and the durable job lifecycle. The worker accepts only signed, timestamped, nonce-protected requests. A job is accepted durably before a `202` response is returned.

### Durable queue and idempotency

The preferred store is Redis/Render Key Value through `REDIS_URL`. The application also supports a SQLite-backed durable queue for a single Render instance when Redis is unavailable. Jobs use explicit states: `queued`, `claimed`, `downloading`, `processing`, `uploading`, `completed`, `failed`, `retry_wait`, and `cancelled`. A lease and heartbeat allow recovery after restarts. Telegram `update_id` is unique and prevents duplicate execution.

### Media extraction policy

The downloader has one deterministic policy per platform:

1. Resolve the canonical public URL.
2. Materialize platform cookies from existing file or base64 environment variables without logging them.
3. Build one stable yt-dlp option set with a pinned version, JavaScript runtime, FFmpeg path, optional proxy, and a validated selector.
4. On YouTube bot-check or HTTP 429, open a circuit breaker and stop alternate-client retry storms.
5. On a media-fragment 403, permit one full re-extraction using the same proxy/cookie identity; never hand a signed `googlevideo` URL to the generic downloader.
6. Public Invidious/Piped instances are disabled by default and may only be enabled explicitly as a best-effort fallback; they are never described as guaranteed.

### Quality model

Buttons are generated from a single quality registry. The registry maps user-facing keys to stable selectors:

- `best`: best available video plus audio, with merged fallback.
- `1080`: best video at or below 1080p plus audio, with a combined-file fallback.
- `720`: best video at or below 720p plus audio, with a combined-file fallback.
- `480`: best video at or below 480p plus audio, with a combined-file fallback.
- `audio`: best audio, converted to MP3.

The callback parser accepts only registry keys. Every selector is unit-tested and every live acceptance URL is inspected to determine whether a requested quality exists. If a source lacks a requested height, the result reports the actual selected height rather than falsely claiming the requested one.

### Circuit breaker

YouTube failures classified as `rate_limited`, `bot_check`, or `login_required` open a process-persistent circuit stored in the durable store. While open, new YouTube jobs fail fast with a safe message. A single canary is allowed after the cooldown. Success closes the circuit; another qualifying failure reopens it with bounded exponential cooldown.

### Storage lifecycle

Files are created inside per-job directories. Startup never wipes all storage. A cleanup process removes only terminal jobs older than `FILE_TTL_SECONDS`, abandoned temporary directories older than a safety window, and expired cookie-upload staging files. Successful Telegram delivery removes the media file immediately unless a download-link retention policy explicitly requires it.

### Security

- Vercel-to-Render authentication uses `WORKER_SHARED_SECRET`; if absent during migration, the existing bot token may verify legacy requests but production health remains degraded until the dedicated secret is set.
- Signatures cover version, timestamp, nonce, context, and body.
- Requests older than five minutes or with reused nonces are rejected.
- Webhook repair becomes an authenticated POST operation; the public status endpoint is read-only and redacted.
- Public URL validation resolves every redirect hop and rejects private, loopback, link-local, reserved, multicast, and credential-bearing destinations.
- Production startup rejects `SECRET_KEY=change-me`.
- Logs redact Telegram tokens, cookie material, authorization headers, signed media query strings, and worker signatures.

## Health and release gate

`/health` returns a safe status plus `gitSha`, pinned yt-dlp version, FFmpeg readiness, JS runtime readiness, queue depth, active jobs, storage statistics, cookie configuration booleans, and YouTube circuit state. It never performs authenticated Telegram calls.

Release requires:

1. Complete automated suite passes.
2. Docker image build passes without mutating dependencies at startup.
3. Vercel and Render report the same expected SHA.
4. TikTok and Instagram supplied URLs inspect and download at one available video quality and as audio.
5. Both supplied YouTube URLs either complete with valid video/audio output or, when YouTube blocks the configured egress, return the classified circuit-breaker error without retries or false success.
6. All visible quality buttons map to valid selectors and the resulting files contain the requested media streams.
7. No secret appears in build or runtime logs.
