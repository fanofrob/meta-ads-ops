"""
Image generation provider abstraction for the static rendering layer.

Provider interface
------------------
ImageGenerator (ABC)
    .name: str
    .generate(prompt, negative_prompt, aspect_ratio, **kwargs) → list[str]

Implementations
---------------
MockImageGenerator   — deterministic placeholder paths; used in tests and spec-only mode
NanoBananaGenerator  — Nano Banana API stub; concrete wiring deferred until API docs available

Factory
-------
get_provider(name=None) → ImageGenerator
    Reads CI_IMAGE_PROVIDER from config if name is not given.
    Always falls back to MockImageGenerator on unknown names.
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import Any


# ─────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────

class ImageGenerator(ABC):
    """Interface all image providers must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable provider identifier string (e.g. 'mock', 'nano_banana')."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        negative_prompt: str,
        aspect_ratio: str = "9:16",
        **kwargs: Any,
    ) -> list[str]:
        """
        Generate one or more images.

        Parameters
        ----------
        prompt          : Positive visual prompt
        negative_prompt : What to avoid
        aspect_ratio    : "9:16" | "1:1" | "4:5" | "16:9"
        **kwargs        : Provider-specific options (seed, steps, style, …)

        Returns
        -------
        list of file paths (local) or public URLs.
        asset_store.save_asset() handles download if a URL is returned.
        """


# ─────────────────────────────────────────────
# Mock provider — always safe, no API calls
# ─────────────────────────────────────────────

class MockImageGenerator(ImageGenerator):
    """
    Deterministic mock that returns placeholder path strings.

    Used in:
    - All offline tests (no API key required)
    - spec-only mode (CI_IMAGE_GENERATION_ENABLED=0)
    - Any environment where CI_IMAGE_PROVIDER is unset or 'mock'

    Returns paths in the form:
        mock://render_{hash}_{ratio}.png
    so downstream code can distinguish mock assets from real ones.
    """

    @property
    def name(self) -> str:
        return "mock"

    def generate(
        self,
        prompt: str,
        negative_prompt: str,
        aspect_ratio: str = "9:16",
        **kwargs: Any,
    ) -> list[str]:
        slug = hashlib.md5(prompt.encode()).hexdigest()[:8]
        ratio_slug = aspect_ratio.replace(":", "x")
        return [f"mock://render_{slug}_{ratio_slug}.png"]


# ─────────────────────────────────────────────
# Nano Banana provider — scaffold
# ─────────────────────────────────────────────

class NanoBananaGenerator(ImageGenerator):
    """
    Nano Banana image generation provider.

    Status: SCAFFOLD — implement _call_api() when official API docs are available.

    Required env vars
    -----------------
    CI_NANO_BANANA_API_KEY      : API key / Bearer token
    CI_NANO_BANANA_ENDPOINT     : API endpoint URL
                                  (default placeholder: https://api.nanobanana.io/v1/generate)

    Expected API shape (to be confirmed from docs)
    -----------------------------------------------
    POST {endpoint}
    Headers: Authorization: Bearer {api_key}
    Body:    {prompt, negative_prompt, aspect_ratio, ...}
    Response: {"images": ["https://cdn.nanobanana.io/…/output.png"]}

    Once the API docs are available:
    1. Implement _call_api(payload) → list[str]
    2. Remove the NotImplementedError
    3. Add CI_NANO_BANANA_API_KEY to Railway env
    """

    @property
    def name(self) -> str:
        return "nano_banana"

    def generate(
        self,
        prompt: str,
        negative_prompt: str,
        aspect_ratio: str = "9:16",
        **kwargs: Any,
    ) -> list[str]:
        from creative_intelligence import config
        if not config.CI_NANO_BANANA_API_KEY:
            raise RuntimeError(
                "CI_NANO_BANANA_API_KEY is not set. "
                "Set it in .env or use CI_IMAGE_PROVIDER=mock."
            )
        return self._call_api({
            "prompt":          prompt,
            "negative_prompt": negative_prompt,
            "aspect_ratio":    aspect_ratio,
            **kwargs,
        })

    def _call_api(self, payload: dict[str, Any]) -> list[str]:  # noqa: ARG002
        # TODO: implement once Nano Banana API docs are available.
        # Expected pattern:
        #   import requests
        #   from creative_intelligence import config
        #   resp = requests.post(
        #       config.CI_NANO_BANANA_ENDPOINT,
        #       json=payload,
        #       headers={"Authorization": f"Bearer {config.CI_NANO_BANANA_API_KEY}"},
        #       timeout=120,
        #   )
        #   resp.raise_for_status()
        #   return resp.json()["images"]   # list of URLs
        raise NotImplementedError(
            "NanoBananaGenerator._call_api() is not implemented yet. "
            "See the docstring for the expected implementation pattern."
        )


# ─────────────────────────────────────────────
# Replicate provider
# ─────────────────────────────────────────────

class ReplicateGenerator(ImageGenerator):
    """
    Image generation via Replicate.

    Required env var
    ----------------
    CI_REPLICATE_API_KEY  : Replicate API token (r8_...)

    Optional env var
    ----------------
    CI_REPLICATE_MODEL    : Model in owner/name or owner/name:version format.
                            Default: stability-ai/stable-diffusion-3.5-large-turbo

    Aspect ratio mapping
    --------------------
    Replicate models accept width/height rather than ratio strings.
    We map our standard ratios to common resolutions (1024-base):
        9:16  → 768 × 1344   (Stories / Reels)
        4:5   → 896 × 1120   (Feed portrait)
        1:1   → 1024 × 1024  (Square)
        16:9  → 1344 × 768   (Landscape)
    """

    _RATIO_TO_DIMS: dict[str, tuple[int, int]] = {
        "9:16":  (768,  1344),
        "4:5":   (896,  1120),
        "1:1":   (1024, 1024),
        "16:9":  (1344, 768),
    }

    @property
    def name(self) -> str:
        return "replicate"

    def generate(
        self,
        prompt: str,
        negative_prompt: str,
        aspect_ratio: str = "9:16",
        **kwargs: Any,
    ) -> list[str]:
        from creative_intelligence import config
        import replicate

        if not config.CI_REPLICATE_API_KEY:
            raise RuntimeError(
                "CI_REPLICATE_API_KEY is not set. "
                "Add it to Railway env vars or use CI_IMAGE_PROVIDER=mock."
            )

        width, height = self._RATIO_TO_DIMS.get(aspect_ratio, (1024, 1024))
        # Per-request model override (from UI model picker); fall back to config default
        model = kwargs.pop("model", None) or config.CI_REPLICATE_MODEL

        client = replicate.Client(api_token=config.CI_REPLICATE_API_KEY)

        # Build input — covers SD3.5, SDXL, Flux, and similar models.
        # Models that don't support a field ignore unknown keys.
        input_payload: dict[str, Any] = {
            "prompt":           prompt,
            "negative_prompt":  negative_prompt,
            "width":            width,
            "height":           height,
            "aspect_ratio":     aspect_ratio,   # some models prefer this
            "num_outputs":      1,
            "output_format":    "png",
            **kwargs,
        }

        output = client.run(model, input=input_payload)

        # Normalize output: Replicate models return either
        # - a single FileOutput object  (e.g. google/nano-banana-pro)
        # - a list/iterator of FileOutput objects  (e.g. SDXL)
        # - a plain string URL
        # FileOutput has a .url() method; str() on it also gives the URL.
        urls: list[str] = []

        def _extract_url(item: Any) -> str:
            if hasattr(item, "url") and callable(item.url):
                return item.url()
            return str(item)

        if hasattr(output, "url") and callable(output.url):
            # Single FileOutput
            url = output.url()
            if url:
                urls.append(url)
        elif isinstance(output, (str, bytes)):
            url = output if isinstance(output, str) else output.decode()
            if url:
                urls.append(url)
        else:
            # List / iterator
            for item in output:
                url = _extract_url(item)
                if url:
                    urls.append(url)

        if not urls:
            raise RuntimeError(
                f"Replicate returned no output for model={model!r}. "
                "Check the model name and API key."
            )
        return urls


# ─────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────

def get_provider(name: str | None = None) -> ImageGenerator:
    """
    Return an ImageGenerator instance.

    Resolution order:
    1. Explicit `name` argument
    2. CI_IMAGE_PROVIDER env var (via config)
    3. Fallback: MockImageGenerator

    Raises
    ------
    Never — unknown names silently fall back to MockImageGenerator.
    """
    from creative_intelligence import config
    resolved = (name or config.CI_IMAGE_PROVIDER or "mock").strip().lower()
    if resolved == "replicate":
        return ReplicateGenerator()
    if resolved == "nano_banana":
        return NanoBananaGenerator()
    return MockImageGenerator()
