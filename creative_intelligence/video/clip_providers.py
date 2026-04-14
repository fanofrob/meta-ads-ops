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
import logging
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


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

    def _get_client(self):
        try:
            import replicate
            return replicate.Client(api_token=self._api_key)
        except ImportError:
            return None

    def _extract_url(self, output: Any, scene_id: int) -> str | None:
        """Extract a video URL from varied Replicate output shapes."""
        if isinstance(output, str) and output.startswith("http"):
            return output
        if hasattr(output, "url"):
            try:
                return str(output.url())
            except TypeError:
                return str(output.url)
        if isinstance(output, (list, tuple)) and output:
            item = output[0]
            if isinstance(item, str) and item.startswith("http"):
                return item
            if hasattr(item, "url"):
                try:
                    return str(item.url())
                except TypeError:
                    return str(item.url)
        try:
            first = next(iter(output))  # type: ignore[arg-type]
            if isinstance(first, str) and first.startswith("http"):
                return first
            if hasattr(first, "url"):
                try:
                    return str(first.url())
                except TypeError:
                    return str(first.url)
        except (StopIteration, TypeError):
            pass
        candidate = str(output) if output is not None else ""
        if candidate.startswith("http"):
            return candidate
        log.error("Replicate returned no usable URL for scene %s. type=%s repr=%r",
                  scene_id, type(output).__name__, output)
        return None

    # Per-model image input field name for image-to-video conditioning.
    # Each model uses a different key to pass the first-frame reference image.
    _IMAGE_FIELD: dict[str, str] = {
        "minimax/video-01-live":        "first_frame_image",
        "minimax/video-01":             "first_frame_image",
        "runwayml/gen-4-turbo":         "prompt_image",
        "runwayml/gen-4.5":             "prompt_image",
        "stability-ai/stable-video-diffusion": "input_image",
        "klingai/kling-video":          "start_image",
        "wan-video/wan2.1-i2v-480p":    "image",
    }

    def _image_field_for_model(self) -> str | None:
        """Return the correct input field name for first-frame image conditioning."""
        # Exact match first, then prefix match for versioned slugs like "minimax/video-01-live:abc123"
        if self._model in self._IMAGE_FIELD:
            return self._IMAGE_FIELD[self._model]
        for prefix, field in self._IMAGE_FIELD.items():
            if self._model.startswith(prefix):
                return field
        return None  # model doesn't support image conditioning

    def create_prediction(
        self,
        prompt: str,
        duration: float = 5.0,
        image_url: str | None = None,
    ) -> str | None:
        """
        Create an async Replicate prediction. Returns prediction_id immediately (~1s).
        Use poll_prediction(prediction_id) to get the result URL when ready.

        image_url: optional product reference image URL.
            When provided and the model supports it, the video starts FROM this
            image (true image-to-video), keeping product appearance consistent.
            minimax/video-01-live uses 'first_frame_image' — this is the
            recommended model for product clips as it locks the first frame.
        """
        if not self._api_key:
            return None
        client = self._get_client()
        if not client:
            return None
        duration_sec = 10 if duration >= 8 else 5
        prediction_input: dict = {"prompt": prompt, "duration": duration_sec}
        if image_url:
            field = self._image_field_for_model()
            if field:
                prediction_input[field] = image_url
                log.info(
                    "Image-to-video: model=%s field=%s url=%s",
                    self._model, field, image_url,
                )
            else:
                log.info(
                    "Model %s has no known image field — skipping image conditioning",
                    self._model,
                )
        try:
            prediction = client.predictions.create(
                model=self._model,
                input=prediction_input,
            )
            log.info("Created Replicate prediction %s for model %s", prediction.id, self._model)
            return prediction.id
        except Exception as exc:
            log.error("Failed to create Replicate prediction: %s", exc, exc_info=True)
            return None

    def poll_prediction(self, prediction_id: str) -> tuple[str, str | None]:
        """
        Poll a Replicate prediction by ID.
        Returns (status, url) where status is 'pending'|'processing'|'succeeded'|'failed'
        and url is the video URL if succeeded.
        """
        if not self._api_key:
            return ("failed", None)
        client = self._get_client()
        if not client:
            return ("failed", None)
        try:
            prediction = client.predictions.get(prediction_id)
            status = prediction.status  # starting|processing|succeeded|failed|canceled
            if status == "succeeded":
                url = self._extract_url(prediction.output, 0)
                return ("succeeded", url)
            if status in ("failed", "canceled"):
                err = getattr(prediction, "error", None) or status
                log.error("Replicate prediction %s %s: %s", prediction_id, status, err)
                return ("failed", None)
            return ("pending", None)  # starting or processing
        except Exception as exc:
            log.error("Failed to poll Replicate prediction %s: %s", prediction_id, exc, exc_info=True)
            return ("failed", None)

    def generate(
        self,
        prompt: str,
        duration: float = 5.0,
        aspect_ratio: str = "9:16",
        scene_id: int = 0,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> str | None:
        """Synchronous generate (blocks until done). Used for single-clip generation."""
        if not self._api_key:
            return None
        client = self._get_client()
        if not client:
            return None
        duration_sec = 10 if duration >= 8 else 5
        try:
            output = client.run(
                self._model,
                input={"prompt": prompt, "duration": duration_sec},
            )
        except Exception as exc:
            log.error("Replicate call failed for scene %s: %s", scene_id, exc, exc_info=True)
            return None
        return self._extract_url(output, scene_id)


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

def get_clip_provider(
    name: str | None = None,
    clip_model: str | None = None,
) -> SceneClipProvider:
    """Return the configured SceneClipProvider.

    clip_model overrides the default CI_CLIP_MODEL env var when provided,
    allowing the UI to select e.g. minimax/video-01-live for image-to-video.
    """
    from creative_intelligence import config
    resolved = name or config.CI_CLIP_PROVIDER
    if resolved == "replicate":
        return ReplicateSceneClipProvider(model=clip_model or None)
    if resolved == "runway":
        return RunwaySceneClipProvider()
    return MockSceneClipProvider()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _default_clip_dir() -> Path:
    from creative_intelligence import config
    return Path(config.CI_CLIP_OUTPUT_DIR)
