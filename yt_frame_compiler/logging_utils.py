from __future__ import annotations

from tqdm import tqdm


class QuietLogger:
    """Minimal yt-dlp logger that suppresses noisy SABR warnings."""

    def debug(self, msg: str) -> None:  # pragma: no cover - yt-dlp callback
        return

    def warning(self, msg: str) -> None:  # pragma: no cover - yt-dlp callback
        lowered = msg.lower()
        if "sabr" in lowered or "formats have been skipped" in lowered:
            return
        tqdm.write(f"WARNING: {msg}")

    def error(self, msg: str) -> None:  # pragma: no cover - yt-dlp callback
        tqdm.write(f"ERROR: {msg}")
