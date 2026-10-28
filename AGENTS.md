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
- **Current YJ production route:** run `docker-compose.novpn.yml` plus `docker-compose.warp.yml`. `web`, `worker`, `bot`, and `cleanup` use `YTDLP_PROXY=http://warp:9002` and `MAX_FRAGMENT_CONNECTIONS_PER_JOB=32`.
- **WARP is YJ-only:** do not connect CryptoVPN, BinanceChecker, or MyWalletChecker to this route. WARP is the active route for YJ; verify `warp=on` from the worker before declaring it healthy.
- **NordVPN is standby only:** preserve `docker-compose.yml`, `.env` NordVPN credentials, and the Germany fallback. Do not delete or alter NordVPN; restore it only when Yousef explicitly requests rollback.
- **Start active YJ route:** `docker compose -f docker-compose.novpn.yml -f docker-compose.warp.yml --env-file .env up -d --build --remove-orphans`.
- **Rollback to NordVPN:** `docker compose -f docker-compose.novpn.yml -f docker-compose.warp.yml down --remove-orphans`, then `docker compose --env-file .env up -d --build`; verify `nordvpn status` and the service health before reporting rollback.
- Container isolation: traffic for YJ is routed through the dedicated WARP container/proxy; do not bind proxy host-wide.
- Never modify `.env` tokens without rotating dependent platform webhooks.
- Do not send test downloads to Telegram; use temporary files inside the worker and delete them after measurement.
- Before changing routing, preserve the active compose command and verify Redis, Bot API, web, worker, and WARP health.

## Performance notes
- Measured active WARP route with the latest saved test URL: `20.10 MiB/s` using 32 concurrent HLS fragments. Keep 32 unless a fresh measurement shows a better stable value.
- Do not claim WARP is faster than NordVPN without comparable same-URL measurements. Do not make WARP permanent as a product decision without Yousef's explicit approval.
