"""Per-timestamp screenshot capture of the main monitor.

Every new timestamp captures one small JPEG snapshot of the primary display so
project logs keep visual context next to each entry. Pixel capture uses mss
(physical-pixel safe on high-DPI Windows systems) and resizing/JPEG encoding
uses Pillow. Both dependencies are imported lazily inside the capture call, so
the pure helpers and every session/path test run without a display or the
dependencies installed.
"""

from __future__ import annotations

import os
import sys


TARGET_HEIGHT = 720  # Saved image height in pixels; captures are never upscaled.
JPEG_QUALITY = 100


class ScreenshotError(RuntimeError):
    """Raised when the screen cannot be captured or encoded as JPEG."""


def compute_target_size(width: int, height: int) -> tuple[int, int]:
    """Return the saved size for a capture.

    The height is fitted to TARGET_HEIGHT while preserving aspect ratio;
    captures at or below the target height keep their native size.
    """
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise ScreenshotError(f"Invalid capture size: {width}x{height}")
    if height <= TARGET_HEIGHT:
        return width, height
    scale = TARGET_HEIGHT / float(height)
    return max(1, round(width * scale)), TARGET_HEIGHT


def _dependencies():
    """Import mss and Pillow on demand with actionable error messages."""
    try:
        import mss
        from PIL import Image
    except Exception as exc:
        if getattr(sys, "frozen", False):
            raise ScreenshotError(
                "The packaged app is missing its screenshot backend (mss/Pillow). "
                "Rebuild the app with: pyinstaller --clean --noconfirm timestamp_gui.spec"
            ) from exc
        raise ScreenshotError(
            "Screenshots need mss and Pillow. "
            "Install them with: pip install mss pillow"
        ) from exc
    return mss, Image


def capture_to_path(output_path: str) -> str:
    """Capture the main monitor and save a 720p-height JPEG.

    Creates the parent folder when needed and returns the written path.
    Raises ScreenshotError when capture or encoding fails; the caller decides
    whether that matters (timestamp creation never depends on it).
    """
    mss, Image = _dependencies()
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    try:
        # mss >= 10 exposes the platform class directly (mss.MSS); older
        # releases only ship the mss() factory function.
        scanner_factory = getattr(mss, "MSS", None) or mss.mss
        with scanner_factory() as scanner:
            # Monitor 0 spans all displays; monitor 1 is the OS primary.
            raw = scanner.grab(scanner.monitors[1])
        # mss delivers BGRA byte order; map it onto RGB pixels directly.
        image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        target_size = compute_target_size(*image.size)
        if target_size != image.size:
            lanczos = getattr(Image, "Resampling", Image).LANCZOS
            image = image.resize(target_size, lanczos)
        image.save(output_path, "JPEG", quality=JPEG_QUALITY)
    except ScreenshotError:
        raise
    except Exception as exc:
        raise ScreenshotError(f"Could not save screenshot: {exc}") from exc
    return output_path
