# Work Progress
- Diagnosed root cause of portal 404 failure: `_fetch_vidara_info` called `api/stream` via direct VPS IP (`64.112.42.39`) without `ytdlp_proxy`, generating tokens locked to host IP. Downloader yt-dlp then failed with HTTP 404 when fetching via NordVPN (`187.40.54.x`).
- Confirmed fix hypothesis: requesting `api/stream` with `proxy=settings.ytdlp_proxy` produces token matching VPN egress with HTTP 200 on both manifest and fragments.
- Patching `app/extractor.py` to ensure all portal extraction (`api/stream`, verify checks, vidmoly, animotv, inspect) consistently respects `settings.ytdlp_proxy`.
- Executing isolated end-to-end download test on `https://laroza1.surf/video.php?vid=14a551998` to confirm genuine download and media validity before user handoff.