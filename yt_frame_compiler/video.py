from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .exceptions import VideoCompilationError
from .models import FrameResult

TARGET_WIDTH = 1920
TARGET_HEIGHT = 1080


def _load_frame(path: Path) -> Any:
    import cv2

    frame = cv2.imread(str(path))
    if frame is None:
        raise VideoCompilationError(f"Unable to load frame image at {path}")
    return frame


def _letterbox_frame(frame: Any) -> Any:
    import cv2
    import numpy as np

    height, width = frame.shape[:2]
    scale = min(TARGET_WIDTH / width, TARGET_HEIGHT / height)

    new_width = max(1, int(width * scale))
    new_height = max(1, int(height * scale))

    resized = cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((TARGET_HEIGHT, TARGET_WIDTH, 3), dtype=np.uint8)

    x_offset = (TARGET_WIDTH - new_width) // 2
    y_offset = (TARGET_HEIGHT - new_height) // 2

    canvas[y_offset : y_offset + new_height, x_offset : x_offset + new_width] = resized
    return canvas


def compile_video(
    frames: Iterable[FrameResult],
    output_path: Path,
    frame_duration_seconds: float,
) -> None:
    ordered_frames = list(frames)
    if not ordered_frames:
        raise VideoCompilationError("No frames available for compilation.")

    if frame_duration_seconds <= 0:
        raise VideoCompilationError("Frame duration must be greater than zero.")

    fps = max(1.0, round(1.0 / frame_duration_seconds, 4))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    import cv2

    codec = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), codec, fps, (TARGET_WIDTH, TARGET_HEIGHT))

    if not writer.isOpened():
        writer.release()
        raise VideoCompilationError(f"Failed to open video writer for {output_path}")

    try:
        for item in ordered_frames:
            frame = _load_frame(item.frame_path)
            prepared = _letterbox_frame(frame)
            writer.write(prepared)
    finally:
        writer.release()
