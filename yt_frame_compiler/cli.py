from __future__ import annotations

import argparse
import concurrent.futures
import math
import multiprocessing
import shutil
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from tqdm import tqdm

from .cache import channel_cache_dir
from .exceptions import ChannelFetchError, DependencyError, FrameExtractionError, VideoCompilationError
from .models import FrameResult


def ensure_dependencies() -> None:
    missing_modules = []
    mapping = {
        "yt_dlp": "yt-dlp",
        "cv2": "opencv-python",
        "PIL": "Pillow",
        "tqdm": "tqdm",
    }
    for module_name, package_name in mapping.items():
        try:
            __import__(module_name)
        except Exception:  # pragma: no cover - environment dependent
            missing_modules.append(package_name)

    missing_binaries = []
    if shutil.which("ffmpeg") is None:
        missing_binaries.append("ffmpeg")

    messages = []
    if missing_modules:
        joined = ", ".join(sorted(set(missing_modules)))
        messages.append(
            f"Missing required dependencies: {joined}. Install them with `pip install -r requirements.txt`."
        )
    if missing_binaries:
        binaries = ", ".join(missing_binaries)
        messages.append(f"Missing required system binaries: {binaries}. Ensure they are installed and on your PATH.")

    if messages:
        raise DependencyError(" ".join(messages))


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yt_frame_compiler",
        description=(
            "Download a frame from every full-length YouTube channel upload and compile them into a slideshow."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--channel",
        dest="channel_url",
        help="YouTube channel URL to process.",
    )
    parser.add_argument(
        "channel_positional",
        nargs="?",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--position",
        "-p",
        type=float,
        default=0.0,
        help="Frame position as a percentage of the video duration (0-100).",
    )
    parser.add_argument(
        "--frame-duration",
        type=float,
        default=0.2,
        help="Duration (in seconds) that each frame should appear in the output video.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("channel_compilation.mp4"),
        help="Output MP4 file path.",
    )
    parser.add_argument(
        "--max-format-height",
        type=int,
        default=720,
        help="Maximum source video height to download in pixels.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Number of concurrent worker processes (auto-detected by default).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of videos processed (useful for testing).",
    )
    parser.add_argument(
        "--browser",
        choices=("chrome", "firefox", "safari", "edge", "brave", "none"),
        default="chrome",
        help="Browser profile to load cookies from for authenticated requests (use 'none' to disable).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse cached metadata and frames to resume an interrupted run.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep temporary working files for debugging.",
    )
    # Backwards compatibility for legacy options (hidden from help).
    parser.add_argument("--output-dir", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--output-name", default=None, help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    if args.channel_url and args.channel_positional:
        parser.error("Provide the channel URL only once (either via --channel or positional argument).")

    args.channel_url = args.channel_url or args.channel_positional
    if not args.channel_url:
        parser.error("Missing channel URL. Provide it with --channel.")
    args.output = Path(args.output)
    if args.browser == "none":
        args.browser = None
    delattr(args, "channel_positional")

    return args


def _normalize_output_path(path: Path) -> Path:
    path = path.expanduser()
    directory = path.parent if path.parent != Path("") else Path.cwd()
    directory = directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)

    filename = path.name if path.name and path.name != "." else "channel_compilation.mp4"
    if not filename.lower().endswith(".mp4"):
        filename = f"{filename}.mp4"
    return directory / filename


def _summarize(
    total_videos: int,
    processed_count: int,
    skipped: List[Tuple[str, str, str]],
    breakdown: Counter,
    output_path: Path,
    total_time_seconds: float,
) -> None:
    tqdm.write("")
    tqdm.write("=== SUMMARY ===")
    tqdm.write(f"Total videos found: {total_videos}")
    tqdm.write(f"Successfully processed: {processed_count}")
    tqdm.write(f"Failed/skipped: {len(skipped)}")
    if breakdown:
        for category, count in breakdown.most_common():
            tqdm.write(f"  - {category}: {count}")
    if skipped:
        for title, reason, _ in skipped:
            tqdm.write(f"    - {title}: {reason}")
    tqdm.write(f"Output file: {output_path}")
    tqdm.write(f"Total time: {_format_duration(total_time_seconds)}")


def _format_duration(seconds: float) -> str:
    if seconds <= 0 or math.isinf(seconds) or math.isnan(seconds):
        return "--:--"
    total_seconds = int(round(seconds))
    minutes, sec = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:d}:{sec:02d}"


def main(argv: List[str] | None = None) -> int:
    argv = argv or sys.argv[1:]

    try:
        ensure_dependencies()
    except DependencyError as exc:
        tqdm.write(str(exc))
        return 1

    args = parse_args(argv)

    if not 0.0 <= args.position <= 100.0:
        tqdm.write("--position must be between 0 and 100.")
        return 1

    if args.limit is not None and args.limit <= 0:
        tqdm.write("--limit must be greater than zero.")
        return 1

    if args.max_workers is not None and args.max_workers <= 0:
        tqdm.write("--max-workers must be greater than zero.")
        return 1

    cpu_default_workers = multiprocessing.cpu_count() or 1
    max_workers = args.max_workers or cpu_default_workers
    auto_limited_workers = False
    if args.browser is None and args.max_workers is None:
        max_workers = min(max_workers, 2)
        auto_limited_workers = True

    try:
        from .youtube import fetch_channel_videos
        from .frames import extract_first_frame
        from .video import compile_video
    except ModuleNotFoundError as exc:
        tqdm.write(f"Missing dependency: {exc}")
        return 1

    REQUEST_SLEEP = 5
    MIN_INTERVAL = 8
    MAX_INTERVAL = 20
    RATE_LIMIT = 3_000_000

    output_candidate = args.output
    if args.output_dir or args.output_name:
        directory = args.output_dir or output_candidate.parent or Path.cwd()
        filename = args.output_name or output_candidate.name or "channel_compilation.mp4"
        output_candidate = Path(directory) / filename

    output_path = _normalize_output_path(output_candidate)

    tqdm.write("Fetching channel videos...")
    overall_start = time.perf_counter()
    try:
        videos = fetch_channel_videos(
            args.channel_url,
            prefer_cache=True,
            force_refresh=False,
            sleep_requests=REQUEST_SLEEP,
            sleep_interval=MIN_INTERVAL,
            max_sleep_interval=MAX_INTERVAL,
            ratelimit=RATE_LIMIT,
            browser=args.browser,
            log=tqdm.write,
        )
    except ChannelFetchError as exc:
        tqdm.write(f"Failed to fetch channel metadata: {exc}")
        return 1

    if args.limit is not None:
        videos = videos[: args.limit]

    if not videos:
        tqdm.write("No videos found to process.")
        return 2

    total_videos = len(videos)
    tqdm.write(f"Fetching channel videos... Done! Found {total_videos} videos")

    cache_dir = channel_cache_dir(args.channel_url)
    frame_dir = cache_dir / "frames"
    if not args.resume and frame_dir.exists():
        try:
            shutil.rmtree(frame_dir)
        except Exception:
            pass
    frame_dir.mkdir(parents=True, exist_ok=True)

    temp_dir_context = None
    temp_root: Path
    processed_map: Dict[str, FrameResult] = {}
    skipped_details: List[Tuple[str, str, str]] = []
    failure_breakdown: Counter = Counter()

    if args.resume:
        reused = 0
        for video in videos:
            cached_frame = frame_dir / f"{video.video_id}.png"
            if cached_frame.exists():
                processed_map[video.video_id] = FrameResult(metadata=video, frame_path=cached_frame)
                reused += 1
        if reused:
            tqdm.write(f"Using cached frames for {reused} video(s).")

    try:
        if args.keep_temp:
            temp_root = Path(
                tempfile.mkdtemp(prefix="yt_frames_", dir=str(output_path.parent))
            )
        else:
            temp_dir_context = tempfile.TemporaryDirectory(prefix="yt_frames_")
            temp_root = Path(temp_dir_context.name)

        download_root = temp_root / "downloads"
        download_root.mkdir(parents=True, exist_ok=True)

        pending_videos = [video for video in videos if video.video_id not in processed_map]
        total_pending = len(pending_videos)
        initial_completed = len(processed_map)

        def categorize_failure(reason: str) -> str:
            lowered = reason.lower()
            if "age" in lowered:
                return "Age-restricted"
            if "unavailable" in lowered or "private" in lowered:
                return "Unavailable"
            if "blocked" in lowered:
                return "Blocked"
            return "Other"

        with tqdm(
            total=total_videos,
            desc="Processing videos",
            unit="video",
            initial=initial_completed,
        ) as progress:
            progress.set_postfix_str(f'Current: -- | Errors: {len(skipped_details)}')

            if total_pending:
                worker_count = max(1, min(max_workers, total_pending))
                if auto_limited_workers and worker_count < cpu_default_workers:
                    tqdm.write(
                        f"Browser cookies not supplied; limiting concurrency to {worker_count} worker(s) to reduce rate limits."
                    )
                tqdm.write(f"Processing {total_pending} video(s) with {worker_count} worker(s)...")

                with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as executor:
                    future_map = {}
                    for video in pending_videos:
                        task_download_dir = download_root / video.video_id
                        future = executor.submit(
                            extract_first_frame,
                            video,
                            task_download_dir,
                            frame_dir,
                            format_limit=max(144, args.max_format_height),
                            position_percent=args.position,
                            browser=args.browser,
                            sleep_requests=REQUEST_SLEEP,
                            sleep_interval=MIN_INTERVAL,
                            max_sleep_interval=MAX_INTERVAL,
                            ratelimit=RATE_LIMIT,
                        )
                        future_map[future] = video

                    for future in concurrent.futures.as_completed(future_map):
                        video = future_map[future]
                        try:
                            result = future.result()
                        except FrameExtractionError as exc:
                            reason = str(exc)
                            category = categorize_failure(reason)
                            failure_breakdown[category] += 1
                            skipped_details.append((video.title, reason, category))
                            tqdm.write(f'ERROR: Unable to process "{video.title}" ({video.video_id}) - {reason}')
                        except Exception as exc:  # pragma: no cover - defensive
                            reason = f"Unexpected error: {exc}"
                            category = categorize_failure(reason)
                            failure_breakdown[category] += 1
                            skipped_details.append((video.title, reason, category))
                            tqdm.write(f'ERROR: Unable to process "{video.title}" ({video.video_id}) - {reason}')
                        else:
                            processed_map[video.video_id] = result

                        progress.update(1)
                        progress.set_postfix_str(f'Current: "{video.title}" | Errors: {len(skipped_details)}')
            else:
                progress.refresh()
                tqdm.write("All frames already cached; skipping downloads.")

        processed = [processed_map[video.video_id] for video in videos if video.video_id in processed_map]
        processed.sort(
            key=lambda item: (
                item.upload_date or item.metadata.upload_date or datetime.max,
                item.metadata.position,
            )
        )
        if not processed:
            tqdm.write("No frames were extracted; exiting without creating a video.")
            return 2

        try:
            compile_video(processed, output_path=output_path, frame_duration_seconds=args.frame_duration)
        except VideoCompilationError as exc:
            tqdm.write(f"Failed to compile video: {exc}")
            return 1
    finally:
        if temp_dir_context is not None:
            temp_dir_context.cleanup()
        elif args.keep_temp:
            tqdm.write(f"Temporary files retained at: {temp_root}")

    total_time = time.perf_counter() - overall_start
    _summarize(
        total_videos=total_videos,
        processed_count=len(processed),
        skipped=skipped_details,
        breakdown=failure_breakdown,
        output_path=output_path,
        total_time_seconds=total_time,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
