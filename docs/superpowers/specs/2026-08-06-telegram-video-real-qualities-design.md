# Telegram Video Delivery and Real Qualities Design

## Goal

Fix two production defects:

1. Videos uploaded through the local Telegram Bot API must render as playable Telegram videos with a real duration, dimensions, thumbnail, and streaming metadata instead of appearing as generic documents.
2. Web and Telegram quality choices must come from the formats actually exposed by each media item, including 1440p, 2160p, and 4320p when available, without inventing 1080p/720p/480p options.

## Constraints

- Preserve the existing Vercel-to-Render architecture.
- Preserve the current YouTube cookies, PO-token provider, proxy support, circuit breaker, and bounded retry behavior.
- Keep `MAX_DURATION_SECONDS=0` and `DOWNLOAD_TIMEOUT_SECONDS=21600` behavior unchanged.
- Do not commit credentials, cookies, tokens, API IDs, or hashes.
- Keep downloads streaming from disk; never read a multi-gigabyte media file fully into memory.
- If metadata extraction fails, the upload may continue without metadata rather than failing the whole job.
- If quality inspection fails, expose only the `best` option; never fabricate resolutions.

## Architecture

### Telegram video identity

Create a focused `app/video_delivery.py` module. It will:

- run `ffprobe` against the final MP4 to read the first video stream width and height plus the format duration;
- run `ffmpeg` with input seeking (`-ss` before `-i`) to capture one JPEG thumbnail no wider than 320 pixels;
- keep the thumbnail under Telegram's size constraints by using a conservative JPEG quality and deleting it after upload;
- return a small immutable metadata object used by `TelegramAPI.send_video_file`.

`TelegramAPI._upload` will support one optional extra multipart file. `send_video_file` will add `duration`, `width`, `height`, `supports_streaming=true`, and the generated `thumbnail` multipart field while continuing to stream the main file through `ProgressFile`.

### Dynamic quality discovery

`app/media_choice.py` remains the central quality registry/normalizer, but static video presets are removed from user-facing fallbacks. It will:

- accept dynamic heights from 144 through 8192;
- deduplicate formats by height;
- reject storyboards and audio-only formats;
- prefer MP4/H.264, then MP4, then other containers;
- estimate final merged size as video size plus the best known audio size for video-only formats;
- produce an exact-first selector with a bounded fallback for the chosen height.

`UniversalExtractor.inspect` will return format records with enough metadata (`height`, `ext`, `vcodec`, `acodec`, `filesize`, and `filesize_approx`) for honest normalization. When no real resolution is found, it returns only `best`.

### Telegram selection flow

The initial URL message remains fast and shows the Video/Audio choice immediately. When the user presses Video:

1. the existing message changes to an inspection progress state;
2. the URL is inspected once;
3. real quality options are normalized and stored in the selection session;
4. the keyboard is rebuilt from `best` plus only the discovered heights;
5. if inspection fails, only `best` is shown with an explicit warning.

A subsequent quality callback is accepted only when its key exists in the stored inspected options.

### YouTube client order

The current forced `mweb` PO-token path is retained as a fallback, not used as the first quality-discovery path.

For YouTube inspection and download:

1. try yt-dlp's default client configuration first, with cookies/proxy/JavaScript runtime but without a forced `player_client`, so all available formats can be returned;
2. if that attempt fails because of the hosting environment, use the existing authenticated `mweb` + PO-token provider path;
3. use the existing `web_safari` fallback last;
4. keep rate-limit and circuit-breaker protection to prevent retry storms.

This order maximizes real quality while preserving the proven production fallback.

## Error handling

- `ffprobe` or thumbnail generation failure: upload the video with whatever metadata was successfully extracted.
- Telegram upload failure: retain the existing safe error reporting and job cleanup behavior.
- Inspection failure: show only `best`; do not expose stale or fixed qualities.
- A callback for a quality not stored in the session: reject it as unavailable.
- YouTube default-client failure: continue to the current provider path without opening the circuit unless all permitted paths fail.

## Verification

- Unit tests prove metadata extraction, thumbnail multipart upload, streaming behavior, 4K/8K normalization, merged-size estimates, callback validation, and inspection fallback behavior.
- Red/green tests prove the old fixed-preset flow fails before implementation.
- Full `pytest`, compilation, dependency audit, secret scan, and production Docker build must pass.
- A production YouTube E2E job must download a media file with audio and video.
- A production quality inspection must expose the real maximum height of a known high-resolution test video.
- A Telegram delivery integration test must confirm the request includes real duration, width, height, and a thumbnail; when a live chat target is unavailable, the multipart contract is verified against a local HTTP receiver in CI.