from __future__ import annotations

import math
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Sequence

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .exceptions import FrameExtractionError
from .logging_utils import QuietLogger
from .models import FrameResult, VideoMetadata

MEDIA_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov"}


def _select_new_media_files(before: Iterable[Path], after: Iterable[Path]) -> List[Path]:
    before_set = {path.resolve() for path in before}
    candidates = []
    for path in after:
        resolved = path.resolve()
        if resolved in before_set:
            continue
        if not resolved.is_file():
            continue
        if resolved.suffix.lower() in MEDIA_EXTENSIONS:
            candidates.append(resolved)
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates


def _calculate_timestamp(duration_seconds: int | None, percent: float) -> float:
    bounded = max(0.0, min(percent, 100.0))
    if not duration_seconds or duration_seconds <= 0:
        return 0.0
    if math.isclose(bounded, 100.0):
        return max(duration_seconds - 0.1, 0.0)
    return duration_seconds * (bounded / 100.0)


def _determine_window(target: float, duration: int | float | None) -> tuple[float, float]:
    window = 1.5  # seconds on either side of the target frame
    start = max(0.0, target - window)
    end = target + window
    if duration:
        end = min(end, float(duration))
    if end - start < 0.75:
        end = start + 0.75
    return start, end


def _build_download_ranges(percent: float, fallback_timestamp: float) -> callable:
    def selector(info_dict: dict, *_args, **_kwargs) -> Sequence[dict]:
        duration = info_dict.get("duration")
        timestamp = _calculate_timestamp(duration, percent) if duration else fallback_timestamp
        start, end = _determine_window(timestamp, duration)
        return [{"start_time": start, "end_time": end}]

    return selector


def _parse_upload_date(value: str | int | float | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.utcfromtimestamp(value)
        except (ValueError, OSError):
            return None
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y%m%d")
        except ValueError:
            return None
    return None


def _extract_frame_ffmpeg(source: Path, timestamp: float, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)

    command = ["ffmpeg", "-loglevel", "error", "-y"]
    if timestamp > 0.0:
        command += ["-ss", f"{timestamp:.3f}"]
    command += ["-i", str(source), "-frames:v", "1", "-q:v", "2", str(destination)]

    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise FrameExtractionError(f"ffmpeg failed to extract frame: {stderr or 'unknown error'}")
    if not destination.exists():
        raise FrameExtractionError(f"ffmpeg did not produce frame at {destination}")


def extract_first_frame(
    metadata: VideoMetadata,
    download_dir: Path,
    frame_dir: Path,
    format_limit: int = 720,
    position_percent: float = 0.0,
    browser: str | None = "chrome",
    sleep_requests: int = 3,
    sleep_interval: int = 5,
    max_sleep_interval: int = 15,
    ratelimit: int = 3_000_000,
) -> FrameResult:
    download_dir.mkdir(parents=True, exist_ok=True)
    frame_dir.mkdir(parents=True, exist_ok=True)

    format_selector = (
        f"bestvideo[height<={format_limit}][ext=mp4]+bestaudio[ext=m4a]/"
        f"best[height<={format_limit}][ext=mp4]/best[ext=mp4]/best"
    )

    base_template = download_dir / f"{metadata.video_id}"
    outtmpl = f"{base_template}.%(ext)s"
    ydl_opts = {
        "quiet": True,
        "ignoreerrors": False,
        "noplaylist": True,
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "format": format_selector,
        "skip_download": False,
        "no_warnings": True,
        "sleep_requests": int(sleep_requests),
        "sleep_interval": int(sleep_interval),
        "max_sleep_interval": int(max_sleep_interval),
        "ratelimit": int(ratelimit),
        "logger": QuietLogger(),
        "download_ranges": _build_download_ranges(position_percent, fallback_timestamp=_calculate_timestamp(metadata.duration, position_percent)),
        "force_keyframes_at_cuts": True,
    }
    if browser:
        ydl_opts["cookiesfrombrowser"] = (browser,)

    before_download = list(download_dir.glob("**/*"))

    info = None
    attempts = 2 if "cookiesfrombrowser" in ydl_opts else 1
    for attempt in range(1, attempts + 1):
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(metadata.url, download=True)
            break
        except DownloadError as exc:
            message = str(exc)
            lowered = message.lower()
            cookies_enabled = "cookiesfrombrowser" in ydl_opts
            if cookies_enabled and ("cookies" in lowered or "browser" in lowered):
                # Retry once without cookies if browser extraction fails.
                ydl_opts = {**ydl_opts}
                ydl_opts.pop("cookiesfrombrowser", None)
                continue
            if "confirm your age" in lowered:
                raise FrameExtractionError("Age restricted (requires authentication)") from exc
            if "rate-limited" in lowered or "try again later" in lowered:
                raise FrameExtractionError("Rate limited by YouTube") from exc
            raise FrameExtractionError(f"Failed to download video: {metadata.title}") from exc

    if info is None:
        raise FrameExtractionError(f"Failed to download video: {metadata.title}")

    after_download = list(download_dir.glob("**/*"))
    new_files = _select_new_media_files(before_download, after_download)

    download_path: Path | None = None
    if info:
        requested_downloads = info.get("requested_downloads") or []
        for item in requested_downloads:
            filepath = item.get("filepath")
            if filepath:
                candidate = Path(filepath)
                if candidate.exists() and candidate.suffix.lower() in MEDIA_EXTENSIONS:
                    download_path = candidate.resolve()
                    break

    if not download_path and new_files:
        download_path = new_files[0]

    if not download_path:
        raise FrameExtractionError(f"Unable to locate downloaded file for video: {metadata.title}")

    frame_path = frame_dir / f"{metadata.video_id}.png"
    duration_seconds = metadata.duration
    if not duration_seconds and info:
        duration_value = info.get("duration")
        if duration_value:
            try:
                duration_seconds = int(duration_value)
            except (TypeError, ValueError):
                duration_seconds = None
    upload_date = metadata.upload_date
    if info:
        upload_date = (
            _parse_upload_date(info.get("upload_date"))
            or _parse_upload_date(info.get("release_timestamp"))
            or upload_date
        )

    timestamp = _calculate_timestamp(duration_seconds, position_percent)
    _extract_frame_ffmpeg(download_path, timestamp, frame_path)

    cleanup_targets = set(new_files)
    cleanup_targets.add(download_path)
    for leftover in cleanup_targets:
        try:
            leftover.unlink(missing_ok=True)
        except Exception:
            continue
    # Attempt to remove empty directory tree.
    try:
        for directory in sorted({path.parent for path in cleanup_targets}, key=lambda p: len(p.parts), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
    except Exception:
        pass

    return FrameResult(metadata=metadata, frame_path=frame_path, upload_date=upload_date)
