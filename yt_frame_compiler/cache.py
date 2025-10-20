from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from .models import VideoMetadata

CACHE_VERSION = 1
DEFAULT_CACHE_ENV = "YT_FRAME_COMPILER_CACHE_DIR"


def _cache_root() -> Path:
    env_path = os.environ.get(DEFAULT_CACHE_ENV)
    if env_path:
        return Path(env_path).expanduser().resolve()
    return Path.home() / ".cache" / "yt_frame_compiler"


def _channel_key(channel_url: str) -> str:
    digest = hashlib.sha256(channel_url.encode("utf-8")).hexdigest()
    return digest[:16]


def channel_cache_dir(channel_url: str) -> Path:
    root = _cache_root()
    key = _channel_key(channel_url)
    return root / key


def _metadata_path(channel_url: str) -> Path:
    return channel_cache_dir(channel_url) / "metadata.json"


def serialize_videos(videos: Iterable[VideoMetadata]) -> dict:
    payload = []
    for video in videos:
        payload.append(
            {
                "video_id": video.video_id,
                "title": video.title,
                "url": video.url,
                "upload_date": video.upload_date.isoformat() if video.upload_date else None,
                "duration": video.duration,
                "position": video.position,
            }
        )
    return {"version": CACHE_VERSION, "generated_at": datetime.utcnow().isoformat(), "videos": payload}


def deserialize_videos(data: dict) -> List[VideoMetadata]:
    items = []
    for item in data.get("videos", []):
        upload_raw = item.get("upload_date")
        upload_date = None
        if upload_raw:
            try:
                upload_date = datetime.fromisoformat(upload_raw)
            except ValueError:
                upload_date = None
        video_id = item.get("video_id")
        url = item.get("url")
        if not video_id or not url:
            continue
        items.append(
            VideoMetadata(
                video_id=video_id,
                title=item.get("title", "Untitled"),
                url=url,
                upload_date=upload_date,
                duration=item.get("duration"),
                position=item.get("position", len(items)),
            )
        )
    return items


def load_cached_metadata(channel_url: str) -> Optional[List[VideoMetadata]]:
    path = _metadata_path(channel_url)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    version = data.get("version")
    if version != CACHE_VERSION:
        return None
    return deserialize_videos(data)


def persist_metadata(channel_url: str, videos: Iterable[VideoMetadata]) -> None:
    cache_dir = channel_cache_dir(channel_url)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "metadata.json"
    payload = serialize_videos(videos)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
