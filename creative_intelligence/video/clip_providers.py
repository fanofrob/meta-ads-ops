"""
Scene-level clip generation providers for Creative Intelligence.

Providers convert a text prompt (plus optional duration) into a local MP4 clip.

Public API
----------
get_clip_provider(name=None) → SceneClipProvider
    Returns the configured provider (reads CI_CLIP_PROVIDER if name is None).

SceneClipProvider.generate(prompt, duration, aspect_ratio, scene_id) → str | None
    Returns local file path of the downloaded MP4, or None on failure.
"""
from __future__ import annotations

import hashlib
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class SceneClipProvider(ABC):
    """Abstract base for scene clip generators."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def generate(
        self,
        prompt: str,
        duration: float = 5.0,
        aspect_ratio: str = "9:16",
        scene_id: int = 0,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> str | None:
        """
        Generate a video clip from a text prompt.

        Returns the local file path of the downloaded MP4, or None on failure.
        """


# ─────────────────────────────────────────────
# Mock provider (offline, no API calls)
# ─────────────────────────────────────────────

class MockSceneClipProvider(SceneClipProvider):
    """Returns a deterministic mock:// path — never touches the network."""

    name = "mock"

    def generate(
        self,
        prompt: str,
        duration: float = 5.0,
        aspect_ratio: str = "9:16",
        scene_id: int = 0,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> str | None:
        slug = hashlib.md5(prompt.encode()).hexdigest()[:8]
        ar = aspect_ratio.replace(":", "x")
        return f"mock://clip_{scene_id}_{slug}_{ar}_{int(duration)}s.mp4"


# ─────────────────────────────────────────────
# Replicate provider (runwayml/gen-4.5 via Replicate API)
# ─────────────────────────────────────────────

class ReplicateSceneClipProvider(SceneClipProvider):
    """
    Generates scene clips using runwayml/gen-4.5 via Replicate.

    Input:  { prompt: str, duration: int }  (text-to-video, no image required)
    Output: output.url() → MP4 URL, downloaded to local disk.

    Reads CI_REPLICATE_API_KEY and CI_CLIP_MODEL from config.
    Override model via constructor for non-Runway Replicate models.
    """

    name = "replicate"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        from creative_intelligence import config
        self._api_key = api_key or config.CI_REPLICATE_API_KEY
        self._model = model or config.CI_CLIP_MODEL

    def generate(
        self,
        prompt: str,
        duration: float = 5.0,
        aspect_ratio: str = "9:16",
        scene_id: int = 0,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> str | None:
        if not self._api_key:
            return None

        try:
            import replicate
        except ImportError:
            return None

        # Clamp to model-supported durations (gen-4.5 supports 5 or 10s)
        duration_sec = 10 if duration >= 8 else 5

        try:
            client = replicate.Client(api_token=self._api_key)
            output = client.run(
                self._model,
                input={"prompt": prompt, "duration": duration_sec},
            )
        except Exception:
            return None

        # Extract URL from varied output shapes
        video_url: str | None = None
        if isinstance(output, str) and output.startswith("http"):
            video_url = output
        elif hasattr(output, "url"):
            try:
                video_url = str(output.url())
            except TypeError:
                video_url = str(output.url)
        elif isinstance(output, (list, tuple)) and output:
            item = output[0]
            if isinstance(item, str) and item.startswith("http"):
                video_url = item
            elif hasattr(item, "url"):
                try:
                    video_url = str(item.url())
                except TypeError:
                    video_url = str(item.url)

        if not video_url:
            return None

        # Download to disk
        dest_dir = output_dir or _default_clip_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        ar = aspect_ratio.replace(":", "x")
        dest = dest_dir / f"scene_{scene_id}_clip_{ar}.mp4"
        try:
            urllib.request.urlretrieve(video_url, str(dest))
            if dest.exists() and dest.stat().st_size > 0:
                return str(dest)
        except Exception:
            pass

        return None


# ─────────────────────────────────────────────
# Runway direct API scaffold (future use)
# ─────────────────────────────────────────────

class RunwaySceneClipProvider(SceneClipProvider):
    """
    Scaffold for Runway's direct API (not via Replicate).
    Not yet implemented — raises NotImplementedError.
    Set CI_RUNWAY_API_KEY and implement _call_api() when ready.
    """

    name = "runway"

    def generate(
        self,
        prompt: str,
        duration: float = 5.0,
        aspect_ratio: str = "9:16",
        scene_id: int = 0,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> str | None:
        raise NotImplementedError(
            "RunwaySceneClipProvider direct API not yet implemented. "
            "Use CI_CLIP_PROVIDER=replicate with CI_CLIP_MODEL=runwayml/gen-4.5 instead."
        )


# ─────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────

def get_clip_provider(name: str | None = None) -> SceneClipProvider:
    """Return the configured SceneClipProvider."""
    from creative_intelligence import config
    resolved = name or config.CI_CLIP_PROVIDER
    if resolved == "replicate":
        return ReplicateSceneClipProvider()
    if resolved == "runway":
        return RunwaySceneClipProvider()
    return MockSceneClipProvider()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _default_clip_dir() -> Path:
    from creative_intelligence import config
    return Path(config.CI_CLIP_OUTPUT_DIR)
