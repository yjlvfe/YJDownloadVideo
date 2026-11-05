# Work Progress

- **2026-11-03:** Diagnosed root cause of portal 404 failure: `_fetch_vidara_info` called `api/stream` via direct VPS IP (`64.112.42.39`) without `ytdlp_proxy`, generating tokens locked to host IP. Downloader yt-dlp then failed with HTTP 404 when fetching via NordVPN (`187.40.54.x`). Patched `app/extractor.py` to route all portal extraction requests through `settings.ytdlp_proxy`.
- **2026-11-04:** Verified live production stack:
  - All 8 Docker containers operational: `yjdownloadvideo-bot-1`, `yjdownloadvideo-worker-1`, `yjdownloadvideo-web-1`, `yjdownloadvideo-cleanup-1`, `yjdownloadvideo-nordvpn-1` (Germany 187.40.54.164), `yjdownloadvideo-socks-1`, `yjdownloadvideo-redis-1`, `yjdownloadvideo-tba-1`.
  - Bot actively polling Telegram local Bot API.
  - SOCKS proxy connectivity verified healthy.
  - Recent jobs completed successfully.
- **2026-11-04:** Synchronizing latest updates to GitHub:
  - Pushed local commits to `origin/main` (`yjlvfe/YJSocialMediaDownload`).
  - Preserved historical August state on `yjlvfe/YJDownloadVideo` via `backup/legacy-august-2026`.
  - Synchronized `main` branch on `yjlvfe/YJDownloadVideo` to align both repositories with current server codebase.
  - Configured `origin` remote with dual-push URLs (`YJDownloadVideo` primary + `YJSocialMediaDownload` mirror) and installed automatic `post-commit` hook for zero-touch auto-pushing.
- **2026-11-05:** Benchmarked, verified, and optimized download & response throughput:
  - Discovered `yjdownloadvideo-worker-1` and `yjdownloadvideo-bot-1` were running a 50-hour-old container process from before recent optimizations; restarted stack so code modifications are actively loaded.
  - Resolved progress reporting bottleneck in `app/worker.py`: yt-dlp `d.get('speed')` only reports single-fragment speed during concurrent HLS fragment downloads (~500 KB/s - 2.5 MB/s), causing Telegram status messages to severely underreport throughput.
  - Added sliding-window aggregate speed and ETA calculator in `_ProgressWriter` to accurately measure cumulative byte deltas over time across all concurrent connections.
  - Switched NordVPN in-place target to Frankfurt (`CONNECT: Frankfurt`) for DE-CIX media peering.
  - Verified real-world download of 458.5 MB HLS media (`https://laroza1.lat/video.php?vid=85d3395d3`): completion time dropped from 270s (4.5 min) to 66.14s (1.1 min), transfer time 44s at 10.62 MB/s (peak 26.05 MB/s), ffprobe validated stream integrity.
