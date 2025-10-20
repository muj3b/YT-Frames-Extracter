class FrameCompilerError(Exception):
    """Base error for the frame compiler."""


class DependencyError(FrameCompilerError):
    """Raised when required dependencies are unavailable."""


class ChannelFetchError(FrameCompilerError):
    """Raised when video metadata cannot be fetched."""


class FrameExtractionError(FrameCompilerError):
    """Raised when a frame cannot be extracted from a video."""


class VideoCompilationError(FrameCompilerError):
    """Raised when the output video cannot be created."""
