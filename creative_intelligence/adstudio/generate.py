"""
Image generation for Ad Studio — Replicate google/nano-banana-pro with a
reference photo (image_input). Called directly here rather than through the ads
pipeline's provider, so Ad Studio stays isolated and uses the exact
nano-banana-pro input fields (no width/height/negative_prompt baggage).
"""
from __future__ import annotations

from typing import Any

MODEL = "google/nano-banana-pro"


class AdGenError(RuntimeError):
    pass


def generate_ad(prompt: str, reference_data_uris: list[str],
                aspect_ratio: str = "4:5") -> str:
    """
    Generate one ad image. Returns a URL or file:// URI for the result.

    reference_data_uris : base64 data URIs (or public URLs) of reference photos.
    """
    from creative_intelligence import config
    import replicate

    if not config.CI_REPLICATE_API_KEY:
        raise AdGenError("CI_REPLICATE_API_KEY is not set.")

    payload: dict[str, Any] = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "output_format": "png",
    }
    if reference_data_uris:
        payload["image_input"] = reference_data_uris

    client = replicate.Client(api_token=config.CI_REPLICATE_API_KEY)
    import time as _t
    last: Exception | None = None
    for attempt in range(3):
        try:
            return _extract_url(client.run(MODEL, input=payload))
        except Exception as exc:
            last = exc
            m = str(exc).lower()
            if "429" in m or "throttled" in m:
                raise AdGenError(str(exc)) from exc   # let caller back off
            # transient network/poll timeouts and 5xx → retry
            if any(k in m for k in ("timed out", "timeout", "read operation",
                                    "connection", "502", "503", "504", "temporarily")):
                _t.sleep(3 * (attempt + 1))
                continue
            raise AdGenError(str(exc)) from exc       # real error
    raise AdGenError(f"failed after retries: {last}")


def _extract_url(out: Any) -> str:
    """Normalise nano-banana-pro output to a single URL / file URI."""
    import tempfile
    from pathlib import Path

    def bytes_to_uri(data: bytes) -> str:
        suffix = ".png" if data[:4] == b"\x89PNG" else ".bin"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix,
                                          dir=tempfile.gettempdir(), prefix="adstudio_")
        tmp.write(data); tmp.close()
        return Path(tmp.name).as_uri()

    def one(item: Any) -> str:
        # New SDK: .url is a str property; older: .url() is callable; some stream bytes
        url = getattr(item, "url", None)
        if callable(url):
            try:
                return url()
            except Exception:
                pass
        elif isinstance(url, str):
            return url
        read = getattr(item, "read", None)
        if callable(read):
            return bytes_to_uri(read())
        if isinstance(item, (bytes, bytearray)):
            return bytes_to_uri(bytes(item))
        return str(item)

    if isinstance(out, (list, tuple)):
        if not out:
            raise AdGenError("Model returned no images")
        return one(out[0])
    return one(out)
