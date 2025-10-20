from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class VideoMetadata:
    video_id: str
    title: str
    url: str
    upload_date: Optional[datetime]
    duration: Optional[int]
    position: int


@dataclass
class FrameResult:
    metadata: VideoMetadata
    frame_path: Path
    upload_date: Optional[datetime]
