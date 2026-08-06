# Telegram Video Delivery and Real Qualities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Telegram uploads render as real videos and expose only the actual qualities available for each media item, including resolutions above 1080p.

**Architecture:** Add an isolated video-delivery metadata helper, extend the custom Telegram multipart uploader, make the quality normalizer fully dynamic, inspect on demand in the Telegram flow, and try yt-dlp's default YouTube client before the existing PO-token fallbacks.

**Tech Stack:** Python 3.12, FastAPI, httpx, yt-dlp, ffmpeg/ffprobe, pytest, GitHub Actions, Docker, Render.

## Global Constraints

- Preserve cookies, proxy, PO-token provider, circuit breaker, retries, secrets, Vercel proxying, and Render deployment behavior.
- Keep media uploads streamed from disk.
- Never invent qualities after an inspection failure.
- Support dynamic heights from 144 through 8192.
- Keep video duration unlimited and retain the six-hour process safety timeout.

---

### Task 1: Telegram video metadata and thumbnail

**Files:**
- Create: `app/video_delivery.py`
- Modify: `app/config.py`
- Test: `tests/test_video_delivery.py`

**Interfaces:**
- Produces: `VideoUploadMetadata`, `prepare_video_upload(path: Path, workdir: Path | None = None) -> VideoUploadMetadata`
- Produces: `ffprobe_location() -> str`

- [ ] **Step 1: Write failing tests**

Create tests that patch `subprocess.run` and assert:

```python
metadata = prepare_video_upload(video_path, tmp_path)
assert metadata.duration == 840
assert metadata.width == 1280
assert metadata.height == 720
assert metadata.thumbnail.is_file()
```

Also test that malformed ffprobe output returns empty metadata instead of raising.

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest -q tests/test_video_delivery.py
```

Expected: import/function failures because the module does not exist.

- [ ] **Step 3: Implement minimal helper**

Implement `ffprobe` JSON parsing and one-frame JPEG generation using `-ss` before `-i`, `scale='min(320,iw)':-2`, and a conservative JPEG quality. Do not read the video into Python memory.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
pytest -q tests/test_video_delivery.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/video_delivery.py app/config.py tests/test_video_delivery.py
git commit -m "feat: prepare Telegram video metadata"
```

### Task 2: Telegram multipart video identity

**Files:**
- Modify: `app/telegram_api.py`
- Modify: `tests/test_telegram_api.py`

**Interfaces:**
- Consumes: `prepare_video_upload`
- Produces: `_upload(..., extra_files: dict[str, Path] | None = None)`

- [ ] **Step 1: Write failing tests**

Add tests asserting `send_video_file` sends `duration`, `width`, `height`, `supports_streaming=true`, and a multipart `thumbnail`, while the main media body is still a `ProgressFile`.

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest -q tests/test_telegram_api.py
```

Expected: payload/attachment assertions fail because only `supports_streaming` is currently sent.

- [ ] **Step 3: Implement minimal upload extension**

Use `contextlib.ExitStack` to open the main media and optional thumbnail in the synchronous upload worker. Delete the generated thumbnail in `send_video_file` after the request completes.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
pytest -q tests/test_telegram_api.py tests/test_video_delivery.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/telegram_api.py tests/test_telegram_api.py
git commit -m "fix: send Telegram videos with real metadata"
```

### Task 3: Dynamic quality model and honest sizes

**Files:**
- Modify: `app/media_choice.py`
- Modify: `tests/test_media_choice.py`

**Interfaces:**
- Produces: dynamic `selector_for_quality`, `is_quality_key`, and `normalize_qualities`

- [ ] **Step 1: Write failing tests**

Add cases for 1440p, 2160p, and 4320p; storyboards; MP4/H.264 preference; and final size estimation from video plus best audio.

```python
assert [q.key for q in normalize_qualities(formats)] == ["720", "2160", "4320"]
assert selector_for_quality("2160").startswith("bestvideo[height=2160]+bestaudio")
assert qualities[1].size == video_size + audio_size
```

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest -q tests/test_media_choice.py
```

Expected: resolutions above 1080 are rejected and size assertions fail.

- [ ] **Step 3: Implement minimal dynamic model**

Replace the 1080 ceiling with a shared 8192 maximum, rank formats by container/codec compatibility, and add audio size only for video-only formats.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
pytest -q tests/test_media_choice.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/media_choice.py tests/test_media_choice.py
git commit -m "fix: derive real media qualities dynamically"
```

### Task 4: Inspect real qualities in Telegram flow

**Files:**
- Modify: `app/telegram_flow.py`
- Modify: `tests/test_telegram_flow.py`

**Interfaces:**
- Consumes: `normalize_qualities`, `selector_for_quality`
- Produces: inspection-driven video quality keyboard

- [ ] **Step 1: Write failing tests**

Add async tests proving that pressing Video calls inspection, stores the returned title and real qualities, prepends only `best`, and falls back to only `best` when inspection raises. Add a parser test accepting `2160` and rejecting values outside the dynamic range.

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest -q tests/test_telegram_flow.py
```

Expected: the current fixed `_PRESET_QUALITIES` behavior fails the new assertions.

- [ ] **Step 3: Implement inspection-on-video-selection**

Remove the fixed preset fallback. On the Video callback, inspect the URL, normalize formats, store the result, and render a keyboard containing `best` plus discovered heights. On inspection failure, show `best` only with a warning.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
pytest -q tests/test_telegram_flow.py tests/test_media_choice.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/telegram_flow.py tests/test_telegram_flow.py
git commit -m "fix: show inspected qualities in Telegram"
```

### Task 5: Return rich format records from inspection

**Files:**
- Modify: `app/extractor.py`
- Modify: `tests/test_extractor.py`
- Modify: `tests/test_main_api.py` if existing API normalization expectations require it

**Interfaces:**
- Produces: `inspect(url)` format records with codec/container/size fields

- [ ] **Step 1: Write failing tests**

Assert that inspection returns raw video format metadata without fixed presets and returns only `best` when no real height is available.

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest -q tests/test_extractor.py
```

Expected: current preset fallback and reduced format records fail.

- [ ] **Step 3: Implement minimal inspection output**

Return `height`, `ext`, `vcodec`, `acodec`, `filesize`, and `filesize_approx` for real video formats. Exclude storyboards and audio-only formats. Do not invent resolutions.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
pytest -q tests/test_extractor.py tests/test_media_choice.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/extractor.py tests/test_extractor.py tests/test_main_api.py
git commit -m "fix: expose real inspected format metadata"
```

### Task 6: Default-first YouTube quality path

**Files:**
- Modify: `app/pot_runtime_patch.py`
- Modify: `tests/test_pot_runtime_patch.py`

**Interfaces:**
- Produces: default-client options with no forced `player_client`
- Preserves: provider `mweb` and `web_safari` fallbacks

- [ ] **Step 1: Write failing tests**

Add tests asserting:

```python
assert "extractor_args" not in default_options
assert attempts == ["default", "primary", "fallback"]
```

Also prove a successful default attempt prevents fallback and a rate limit from the provider path stops retry storms.

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest -q tests/test_pot_runtime_patch.py
```

Expected: current primary-first order and forced client fail.

- [ ] **Step 3: Implement minimal attempt order**

Create default YouTube options from the original yt-dlp options, remove inherited `extractor_args`, retain cookies/proxy/JS runtime, and try default before the current provider/fallback paths for both metadata and download.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
pytest -q tests/test_pot_runtime_patch.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/pot_runtime_patch.py tests/test_pot_runtime_patch.py
git commit -m "fix: prefer full YouTube format discovery"
```

### Task 7: Full verification and production checks

**Files:**
- Modify only if verification reveals a root-cause regression.

- [ ] **Step 1: Run focused tests**

```bash
pytest -q tests/test_video_delivery.py tests/test_telegram_api.py tests/test_media_choice.py tests/test_telegram_flow.py tests/test_extractor.py tests/test_pot_runtime_patch.py
```

- [ ] **Step 2: Run repository gate**

```bash
python -m compileall -q app scripts
pytest -q
pip-audit -r requirements.txt
```

Run the repository's existing secret scan and Docker build workflow.

- [ ] **Step 3: Open PR and wait for CI**

Create a draft PR to `main`, verify every required check, then mark ready.

- [ ] **Step 4: Merge to main**

Merge only after all checks pass and the PR head SHA is unchanged.

- [ ] **Step 5: Verify Render deployment**

Confirm the deployment for the merge commit reaches `live` without replacing environment variables.

- [ ] **Step 6: Run production E2E**

Through the public Vercel endpoint:

- inspect a known high-resolution YouTube video and confirm the returned maximum real height is above 1080;
- create a download for one advertised height and verify the output with `ffprobe`;
- run the existing short YouTube audio+video E2E check;
- verify the Telegram multipart contract includes duration, width, height, streaming, and thumbnail.

## Plan Self-Review

- Covers both uploaded specifications completely.
- Contains no fixed quality fallback.
- Keeps the proven YouTube provider paths as fallbacks.
- Keeps files focused by placing probing/thumbnail work in a new module.
- Every production change is preceded by a failing test.
- No secret or environment replacement operation is required.