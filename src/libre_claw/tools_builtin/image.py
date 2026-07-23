# Copyright 2026 Kroonen AI (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import base64
import io
import math
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from libre_claw.core.session import UserAttachment
from libre_claw.core.tools import BaseTool, ToolResult, register_tool


DEFAULT_MAX_DIMENSION = 1_600
MIN_MAX_DIMENSION = 64
MAX_MAX_DIMENSION = 2_048
MAX_IMAGE_FILE_BYTES = 50 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_PREVIEW_BYTES = 1_500_000
JPEG_QUALITY = 88


@register_tool
class ViewImageTool(BaseTool):
    name = "view_image"
    description = (
        "Inspect a local image by attaching a bounded visual preview to the model. "
        "Use shell tools to extract a video or PDF frame first, then view the resulting image."
    )
    parameters = {
        "path": {"type": "string", "description": "Absolute or relative image file path"},
        "frame": {
            "type": "integer",
            "description": "Zero-based frame for animated or multi-frame images",
            "default": 0,
        },
        "max_dimension": {
            "type": "integer",
            "description": (
                "Maximum preview width or height in pixels, "
                f"from {MIN_MAX_DIMENSION} to {MAX_MAX_DIMENSION}"
            ),
            "default": DEFAULT_MAX_DIMENSION,
        },
    }
    required = ("path",)
    permission_level = "allow"

    async def execute(
        self,
        path: str,
        frame: int = 0,
        max_dimension: int = DEFAULT_MAX_DIMENSION,
    ) -> ToolResult:
        try:
            return await asyncio.to_thread(
                self._view,
                path,
                frame,
                max_dimension,
            )
        except Exception as exc:
            return ToolResult(error=str(exc))

    def _view(self, path: str, frame: int, max_dimension: int) -> ToolResult:
        if not isinstance(frame, int) or isinstance(frame, bool):
            return ToolResult(error="frame must be an integer")
        if frame < 0:
            return ToolResult(error="frame must be >= 0")
        if not isinstance(max_dimension, int) or isinstance(max_dimension, bool):
            return ToolResult(error="max_dimension must be an integer")
        if max_dimension < MIN_MAX_DIMENSION:
            return ToolResult(error=f"max_dimension must be >= {MIN_MAX_DIMENSION}")
        if max_dimension > MAX_MAX_DIMENSION:
            return ToolResult(error=f"max_dimension must be <= {MAX_MAX_DIMENSION}")

        resolved = self.resolve_path(path)
        if not resolved.exists():
            return ToolResult(error=f"File does not exist: {resolved}")
        if not resolved.is_file():
            return ToolResult(error=f"Path is not a file: {resolved}")
        file_bytes = resolved.stat().st_size
        if file_bytes > MAX_IMAGE_FILE_BYTES:
            return ToolResult(
                error=(
                    f"Image file exceeds {MAX_IMAGE_FILE_BYTES} bytes; "
                    "resize or extract a smaller preview first"
                )
            )

        try:
            return _image_result(
                resolved,
                frame=frame,
                max_dimension=max_dimension,
                file_bytes=file_bytes,
            )
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
            return ToolResult(error=f"Could not read image {resolved}: {exc}")


def _image_result(
    path: Path,
    *,
    frame: int,
    max_dimension: int,
    file_bytes: int,
) -> ToolResult:
    with Image.open(path) as source:
        original_format = str(source.format or "unknown")
        original_mode = source.mode
        frame_count = int(getattr(source, "n_frames", 1) or 1)
        if frame >= frame_count:
            return ToolResult(
                error=f"frame {frame} was requested, but the image has {frame_count} frame(s)"
            )
        source.seek(frame)
        original_width, original_height = source.size
        if original_width * original_height > MAX_IMAGE_PIXELS:
            return ToolResult(
                error=(
                    f"Image has {original_width * original_height} pixels; "
                    f"the limit is {MAX_IMAGE_PIXELS}. Resize it before viewing."
                )
            )

        oriented = ImageOps.exif_transpose(source)
        oriented.load()
        preview = _rgb_preview(oriented)
        preview.thumbnail(
            (max_dimension, max_dimension),
            Image.Resampling.LANCZOS,
        )
        preview, preview_bytes = _bounded_jpeg(preview)
        preview_width, preview_height = preview.size

    encoded = base64.b64encode(preview_bytes).decode("ascii")
    preview_name = f"{path.stem}-frame-{frame}.jpg" if frame_count > 1 else f"{path.stem}-preview.jpg"
    attachment = UserAttachment(
        media_type="image/jpeg",
        data=encoded,
        filename=preview_name,
        path=str(path),
    )
    resized = (preview_width, preview_height) != (original_width, original_height)
    content = (
        f"Attached image preview for {path}: {preview_width}x{preview_height} JPEG"
        f" (original {original_format} {original_width}x{original_height} {original_mode},"
        f" frame {frame + 1}/{frame_count})."
    )
    return ToolResult(
        content=content,
        metadata={
            "path": str(path),
            "original_format": original_format,
            "original_mode": original_mode,
            "original_width": original_width,
            "original_height": original_height,
            "frame": frame,
            "frame_count": frame_count,
            "preview_width": preview_width,
            "preview_height": preview_height,
            "preview_bytes": len(preview_bytes),
            "preview_byte_limit": MAX_PREVIEW_BYTES,
            "source_bytes": file_bytes,
            "media_type": "image/jpeg",
            "resized": resized,
        },
        attachments=(attachment,),
    )


def _rgb_preview(image: Image.Image) -> Image.Image:
    if "A" not in image.getbands():
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    background.alpha_composite(rgba)
    return background.convert("RGB")


def _bounded_jpeg(image: Image.Image) -> tuple[Image.Image, bytes]:
    preview = image
    quality = JPEG_QUALITY
    for _ in range(8):
        buffer = io.BytesIO()
        preview.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
        )
        encoded = buffer.getvalue()
        if len(encoded) <= MAX_PREVIEW_BYTES:
            return preview, encoded

        scale = min(0.9, math.sqrt(MAX_PREVIEW_BYTES / len(encoded)) * 0.92)
        width, height = preview.size
        resized = (
            max(1, int(width * scale)),
            max(1, int(height * scale)),
        )
        if resized == preview.size:
            quality = max(45, quality - 10)
        else:
            preview = preview.resize(resized, Image.Resampling.LANCZOS)

    return preview, encoded
