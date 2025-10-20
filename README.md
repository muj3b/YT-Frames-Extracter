# YouTube Channel Video Frame Compiler

Command-line utility that downloads frames from every full-length video on a YouTube channel and stitches them into a single MP4 slideshow.

## Features
- Filters out YouTube Shorts and live streams.
- Chronological slideshow (oldest to newest) at 1080p with configurable frame duration and frame position.
- Parallel downloads and frame extraction with ffmpeg accurate-seek and clipped downloads (only a few seconds per video).
- Graceful handling of private/unavailable and age-restricted videos (skipped unless cookies are supplied).
- Metadata caching, resumable processing, and browser-cookie authentication to dodge rate limits.
- Clear tqdm-powered progress bar with ETA, live status, and concise logging (no SABR spam).
- Configurable output directory, filename, and maximum download resolution.

## Requirements
- Python 3.9 or newer.
- `ffmpeg` available on the system path (required by `yt-dlp` for muxing formats).
- Install Python dependencies:

  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  ```

## Usage

```
python -m yt_frame_compiler --channel <CHANNEL_URL> [options]
```

### Options
- `--channel URL` *(required)* – YouTube channel URL to process.
- `--position/-p PERCENT` – choose which point in each video to sample (0–100, default: `0`).
- `--frame-duration SECONDS` – how long each frame displays in the final video (default: `0.2`).
- `--output PATH` – output MP4 file path (default: `channel_compilation.mp4`).
- `--max-format-height PIXELS` – cap download resolution to this height (default: `720`).
- `--max-workers NUM` – override automatic worker count for parallel extraction.
- `--limit NUM` – process only the first `NUM` videos (handy for testing).
- `--browser NAME` – browser profile to source cookies from (`chrome`, `firefox`, `safari`, `edge`, `brave`, `none`).
- `--resume` – reuse cached metadata and frames to continue an interrupted run.
- `--keep-temp` – preserve temporary downloads and extracted frames for debugging.

### Example

```bash
python -m yt_frame_compiler --channel https://www.youtube.com/@ExampleChannel \
  --output ./out/timeline.mp4 \
  --frame-duration 0.25 \
  --position 50 \
  --browser firefox \
  --max-workers 6
```

## How It Works
1. Fetches channel metadata via `yt-dlp`, skipping Shorts, live streams, and missing uploads.
2. Downloads each eligible video (capped at the configured resolution) into isolated worker directories.
3. Uses `ffmpeg` to seek directly to the requested timestamp and capture a single frame, storing results in a persistent cache.
4. Letterboxes frames to 1920×1080 while keeping aspect ratio and writes them to an MP4 at an FPS derived from the frame duration.

## Error Handling
- Invalid URLs, private videos, missing dependencies, and network issues surface with clear messages.
- Videos that fail to download or process are skipped and listed in the summary.

## Notes
- Rate limiting and transient errors trigger automatic retries with exponential backoff plus staggered request delays.
- Browser cookies are loaded automatically (default Chrome). Specify `--browser none` to disable cookie extraction and skip age-restricted videos.
- Running without cookies automatically limits concurrency to protect against rate limiting; supply cookies and `--max-workers` to push throughput higher.
- Want to authenticate? Sign in to YouTube in your browser, then run with `--browser chrome` (or your browser of choice). On macOS you may need to allow Terminal *Full Disk Access* for Safari cookies. See the [`yt-dlp` cookies guide](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies) for manual export instructions.
- Video metadata is cached under `~/.cache/yt_frame_compiler/` (override with `YT_FRAME_COMPILER_CACHE_DIR`). Delete the channel sub-directory to refresh.
- Frame captures are persisted per channel to unlock `--resume`; rerun with that flag after an interruption to process only the missing videos.
- Temporary download folders are cleaned up automatically unless `--keep-temp` is specified.
