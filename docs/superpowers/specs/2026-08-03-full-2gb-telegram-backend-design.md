# Full 2GB Telegram Backend Design

## Architecture

Vercel remains the public website and Telegram webhook receiver, but it never downloads media. URL updates and web jobs are forwarded to the existing Render worker. Render downloads and merges media with yt-dlp/FFmpeg, stores website results in R2, and sends Telegram results through a Telegram Bot API server running in `--local` mode inside the same Docker container.

## Telegram data flow

1. Telegram Local Bot API sends the webhook update to Vercel.
2. Vercel validates the webhook secret and forwards the update JSON to Render.
3. Render acknowledges the job, deduplicates `update_id`, and processes one large job at a time.
4. The worker downloads the selected/original media up to `2_000_000_000` bytes.
5. The worker sends the absolute local path to the co-located local Bot API server.
6. The worker deletes the temporary directory in `finally`.

## Website data flow

1. Browser calls existing Vercel API routes.
2. Vercel proxies metadata/job requests to Render.
3. Render downloads and uploads the file to R2 with multipart upload.
4. Render returns a short-lived signed R2 URL.
5. Browser downloads directly from R2, never through Vercel.

## Limits and safety

- Maximum file size: `2_000_000_000` bytes.
- Maximum duration: 7200 seconds.
- No playlists, live streams, private-network URLs, or credential-bearing URLs.
- No tokens, API hashes, cookies, or signed URLs in logs.
- One global large-file job on the free Render instance and one active job per Telegram chat.
- Temporary local storage is disposable; website results persist only in R2.
