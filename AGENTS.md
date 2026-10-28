# YJSocialMediaDownload

Multi-platform social media video and media download service backed by Docker and MicroSocks proxy integration.

## Dev environment
- Docker & Docker Compose: `docker-compose.yml`, `Dockerfile`, `Dockerfile.microsocks`
- Config: `.env` for service ports and credentials
- Working directory: `/root/Projects/YJSocialMediaDownload`

## Service & deployment commands
- Deploy/Update: `./deploy.sh`
- Docker compose check: `docker compose ps`
- View logs: `docker compose logs -f --tail=100`

## Conventions & invariants
- **Current YJ production route:** run the default `docker-compose.yml` (now WARP-based). `web`, `worker`, `bot`, and `cleanup` use `YTDLP_PROXY=http://warp:9002` and `MAX_FRAGMENT_CONNECTIONS_PER_JOB=32`.
- `docker-compose.novpn.yml` and `docker-compose.warp.yml` are retained as compatibility/reference files; the default file is the source of truth for boot and deployment.
- `docker-compose.nordvpn.yml` is the preserved manual rollback file and must never be auto-started.
- `deploy.sh` is the canonical deployment command and uses only the WARP default compose.
- **WARP is YJ-only:** do not connect CryptoVPN, BinanceChecker, or MyWalletChecker to this route. WARP is the active route for YJ; verify `warp=on` from the worker before declaring it healthy.
- **NordVPN is standby only:** preserve `docker-compose.nordvpn.yml`, `.env` NordVPN credentials, and the Germany fallback. Do not delete or alter NordVPN; restore it only when Yousef explicitly requests rollback.
- **Start active YJ route:** `docker compose --env-file .env up -d --build --remove-orphans`.
- **Rollback to NordVPN:** `docker compose down --remove-orphans`, then `docker compose -f docker-compose.nordvpn.yml --env-file .env up -d --build --remove-orphans`; verify `nordvpn status` and service health before reporting rollback.
- Container isolation: traffic for YJ is routed through the dedicated WARP container/proxy; do not bind proxy host-wide.
- Never modify `.env` tokens without rotating dependent platform webhooks.
- Download routing is split: yt-dlp uses `YTDLP_PROXY=http://warp:9002`; Telegram upload uses the local Bot API `http://tba:8081` with `ProgressHTTPXRequest(trust_env=False)`, so upload stays on direct VPS egress and never inherits the download proxy.
- Queue admission is unlimited (`MAX_ACTIVE_JOBS_PER_IP=0`, `MAX_ACTIVE_JOBS_PER_TELEGRAM_USER=0`); the worker execution pool remains bounded to protect the single WARP egress. Forty requests are accepted into Redis and processed FIFO, not rejected at intake.
- Do not send test downloads to Telegram; use temporary files inside the worker and delete them after measurement.
- Before changing routing, preserve the active compose command and verify Redis, Bot API, web, worker, and WARP health.

## Performance notes
- Measured active WARP route with the latest saved test URL: `20.10 MiB/s` using 32 concurrent HLS fragments. Keep 32 unless a fresh measurement shows a better stable value.
- Do not claim WARP is faster than NordVPN without comparable same-URL measurements. Do not make WARP permanent as a product decision without Yousef's explicit approval.
- Treat transient Redis socket timeouts and empty downloader artifacts as retryable transport failures; use bounded Redis socket/retry settings and retry the same yt-dlp request before marking a job failed. Do not rotate WARP or NordVPN for destination-level HTTP errors.
- Keep provider-specific permanent errors non-retryable; retries are for transport/empty-artifact failures only, so one blocked site does not create a request storm for every site.
