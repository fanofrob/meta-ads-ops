"""
Video frame selector — SCAFFOLD for future video preview support.

In v1, video creatives use thumbnail_url for analysis.
This module is a placeholder for future first-frame extraction
from video assets (e.g., via Meta's video thumbnails API or ffmpeg).

Not invoked by the core pipeline. Importing is safe.
"""
from __future__ import annotations

from typing import Any


def get_video_thumbnail_url(creative_id: str, dry_run: bool = True) -> str | None:
    """Resolve the best thumbnail URL for a video creative.

    v1: returns None (scaffold).
    Future: call Meta API for video thumbnail or extract from video_url.
    """
    # Future implementation:
    # 1. GET /{video_id}?fields=thumbnails,picture
    # 2. Return highest-resolution thumbnail URL
    return None


def extract_first_frame(video_url: str) -> bytes | None:
    """Extract the first frame from a video URL using ffmpeg. SCAFFOLD."""
    # Future implementation via subprocess + ffmpeg:
    # ffmpeg -i {video_url} -vf "select=eq(n\,0)" -vframes 1 frame.jpg
    raise NotImplementedError("Video frame extraction not yet implemented.")


def select_representative_frame(
    video_url: str,
    strategy: str = "first",
) -> bytes | None:
    """Select a representative frame from a video. SCAFFOLD.

    strategy: 'first' | 'peak_motion' | 'thumbnail'
    """
    raise NotImplementedError("Representative frame selection not yet implemented.")
