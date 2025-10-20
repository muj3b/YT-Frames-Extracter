from __future__ import annotations

from datetime import datetime
from typing import Callable, Iterable, List, Optional

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .cache import load_cached_metadata, persist_metadata
from .exceptions import ChannelFetchError
from .logging_utils import QuietLogger
from .models import VideoMetadata


SHORT_URL_SEGMENT = "/shorts/"
MIN_DURATION_SECONDS = 60


def _parse_upload_date(raw: str | int | float | None) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.utcfromtimestamp(raw)
        except (ValueError, OSError):
            return None
    if isinstance(raw, str):
        try:
            return datetime.strptime(raw, "%Y%m%d")
        except ValueError:
            return None
    return None


def _entry_to_metadata(entry: dict, position: int) -> VideoMetadata | None:
    url = entry.get("webpage_url") or entry.get("url")
    if not url:
        return None
    if SHORT_URL_SEGMENT in url.lower():
        return None

    duration = entry.get("duration")
    if duration is not None:
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            duration = None
    if duration is not None and duration < MIN_DURATION_SECONDS:
        return None

    upload_date = _parse_upload_date(entry.get("upload_date") or entry.get("timestamp") or entry.get("release_timestamp"))
    video_id = entry.get("id") or entry.get("display_id")
    if not video_id:
        return None

    title = entry.get("title") or "Untitled"
    return VideoMetadata(
        video_id=video_id,
        title=title,
        url=url,
        upload_date=upload_date,
        duration=duration,
        position=position,
    )


def _iter_entries(payload: dict) -> Iterable[dict]:
    stack = [payload]
    while stack:
        current = stack.pop()
        entries = current.get("entries")
        if entries:
            for item in reversed(entries):  # maintain order
                if item:
                    stack.append(item)
            continue
        yield current


def fetch_channel_videos(
    channel_url: str,
    max_retries: int = 3,
    retry_delay_seconds: float = 3.0,
    prefer_cache: bool = True,
    force_refresh: bool = False,
    sleep_requests: int = 3,
    sleep_interval: int = 5,
    max_sleep_interval: int = 15,
    ratelimit: int = 3_000_000,
    browser: str | None = "chrome",
    log: Optional[Callable[[str], None]] = None,
) -> List[VideoMetadata]:
    log = log or (lambda message: None)
    cached: List[VideoMetadata] | None = None
    if prefer_cache:
        cached = load_cached_metadata(channel_url)
        if cached and not force_refresh:
            return cached

    backlog: List[VideoMetadata] = []
    opts = {
        "quiet": True,
        "skip_download": True,
        "ignoreerrors": True,
        "noplaylist": False,
        "extract_flat": True,
        "sleep_requests": int(sleep_requests),
        "sleep_interval": int(sleep_interval),
        "max_sleep_interval": int(max_sleep_interval),
        "ratelimit": int(ratelimit),
        "no_warnings": True,
        "logger": QuietLogger(),
    }
    if browser:
        opts["cookiesfrombrowser"] = (browser,)

    for attempt in range(1, max_retries + 1):
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(channel_url, download=False)
        except DownloadError as exc:  # pragma: no cover
            message = str(exc)
            cookies_enabled = "cookiesfrombrowser" in opts
            if cookies_enabled and ("cookies" in message.lower() or "browser" in message.lower()):
                log(
                    f"Browser cookie extraction failed ({message}). Retrying without cookies."
                )
                opts.pop("cookiesfrombrowser", None)
                continue
            if attempt == max_retries:
                if cached:
                    return cached
                raise ChannelFetchError(str(exc)) from exc
            # Simple back-off for transient errors.
            import time

            sleep_for = retry_delay_seconds * attempt
            ydl_error = getattr(exc, "msg", str(exc))
            log(
                f"Encountered error fetching channel data ({ydl_error}). "
                f"Retrying in {sleep_for:.1f}s..."
            )
            time.sleep(sleep_for)
            continue

        if not info:
            if cached:
                return cached
            raise ChannelFetchError("Failed to fetch channel information.")

        for position, entry in enumerate(_iter_entries(info)):
            if not entry:
                continue

            if entry.get("live_status") in {"is_live", "is_upcoming"}:
                continue

            metadata = _entry_to_metadata(entry, position)
            if metadata:
                backlog.append(metadata)

        break

    backlog.sort(key=lambda item: (item.upload_date or datetime.max, item.position))
    if backlog:
        try:
            persist_metadata(channel_url, backlog)
        except Exception:
            pass
        return backlog

    if cached:
        return cached
    raise ChannelFetchError("No eligible videos found on the channel.")
