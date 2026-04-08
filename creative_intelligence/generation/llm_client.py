"""
Provider-agnostic LLM client for creative generation.

Architecture:
  LLMClient (abstract interface)
    └── OpenAILLMClient     (production — OPENAI_API_KEY)
    └── AnthropicLLMClient  (production — ANTHROPIC_API_KEY)
    └── MockLLMClient       (testing / dry-run)

Swap providers by implementing LLMClient and registering in get_llm_client().
All generation modules call get_llm_client() — never import a specific provider directly.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

from creative_intelligence import config


class LLMClient(ABC):
    """Abstract LLM client interface."""

    provider: str = "unknown"
    model: str = "unknown"

    @abstractmethod
    def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.8,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        """Send a chat completion request. Returns the response text."""

    def complete_json(
        self,
        system: str,
        user: str,
        temperature: float = 0.4,
        max_tokens: int | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Send a request expecting JSON. Returns parsed object."""
        raw = self.complete(system, user, temperature, max_tokens, json_mode=True)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Best-effort extraction if model added markdown fences.
            stripped = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(stripped)


def _extract_product_from_prompt(user: str) -> str:
    """Extract the product name hint from a generation prompt."""
    # Looks for 'product_name = X' or 'Name: X' or 'Product: X' in the prompt.
    for pattern in (
        r"PRODUCT NAME REQUIRED[^\n]*?:\s*([^\n\.]+)",
        r"(?:Name|Product)\s*:\s*([^\n]+)",
    ):
        m = re.search(pattern, user, re.IGNORECASE)
        if m:
            name = m.group(1).strip().rstrip(".")
            if name and "placeholder" not in name.lower():
                return name
    return "the product"


def _mock_hooks(product: str, count: int) -> list[str]:
    """Return structurally varied mock hooks for dry-run testing."""
    templates = [
        f"These are the most intensely flavourful {product} you will ever taste.",
        f"Taste {product} the way nature actually intended — nothing grocery stores carry comes close.",
        f"What grocery store {product} wishes it could be. Hand-picked and shipped same-day.",
        f"You've never tasted {product} like this. We guarantee it.",
        f"Ever wondered why {product} from the supermarket never quite delivers? This is why.",
        f"Grown at peak altitude and harvested the morning your order ships — this is real {product}.",
        f"Try the {product} that's been making people cancel their grocery orders for good.",
        f"Sweet, complex, nothing artificial — {product} the way it should have always tasted.",
        f"Why do our customers say grocery-store {product} is ruined for them forever? Taste and see.",
        f"Direct from the farm to your door: {product} at full ripeness, not picked-green.",
        f"The {product} you grew up eating was a compromise. This one isn't.",
        f"Farm-fresh {product}, harvested at peak sweetness, delivered before it loses an ounce of flavour.",
        f"What happens when {product} is actually allowed to ripen on the tree? Pure magic.",
        f"One bite of real tree-ripened {product} and the grocery store version is gone from your life.",
        f"Experience {product} the way farmers' families eat it — you won't go back.",
    ]
    return [templates[i % len(templates)] for i in range(count)]


class MockLLMClient(LLMClient):
    """Returns deterministic, product-aware fixture responses for tests and dry-runs."""

    provider = "mock"
    model    = "mock-v1"

    def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.8,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        # Detect requested count from the prompt ("Generate exactly N hooks").
        count = 5
        m = re.search(r"Generate exactly (\d+) hooks", user, re.IGNORECASE)
        if m:
            count = int(m.group(1))

        product = _extract_product_from_prompt(user)
        hooks = _mock_hooks(product, count)

        if json_mode:
            return json.dumps({"hooks": hooks})
        return "\n".join(f"- {h}" for h in hooks)


class OpenAILLMClient(LLMClient):
    """OpenAI chat completions client."""

    provider = "openai"

    def __init__(self, model: str | None = None):
        self.model = model or config.CI_LLM_MODEL or "gpt-4o-mini"

    def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.8,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        try:
            import openai
        except ImportError:
            raise RuntimeError("openai package required. pip install openai")

        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not set")

        client = openai.OpenAI(api_key=config.OPENAI_API_KEY)
        kwargs: dict[str, Any] = {
            "model":       self.model,
            "messages":    [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            "temperature": temperature,
            "max_tokens":  max_tokens or config.CI_LLM_MAX_TOKENS,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""


class AnthropicLLMClient(LLMClient):
    """Anthropic Messages API client."""

    provider = "anthropic"

    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("CI_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.8,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package required. pip install anthropic")

        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        client = anthropic.Anthropic(api_key=api_key)

        # Append JSON instruction to system prompt when JSON mode is requested.
        effective_system = system
        if json_mode:
            effective_system = system + "\nRespond with valid JSON only. No markdown fences."

        message = client.messages.create(
            model=self.model,
            max_tokens=max_tokens or config.CI_LLM_MAX_TOKENS,
            temperature=temperature,
            system=effective_system,
            messages=[{"role": "user", "content": user}],
        )
        return message.content[0].text if message.content else ""


# Bring os into scope for AnthropicLLMClient
import os  # noqa: E402 (imported here to avoid re-ordering imports at top of file)


def get_llm_client(provider: str | None = None, dry_run: bool = False) -> LLMClient:
    """Return the appropriate LLMClient.

    provider: 'openai' | 'anthropic' | 'mock' | None (auto-select)
    dry_run:  if True, always return MockLLMClient

    Auto-selection priority: openai → anthropic → mock
    """
    if dry_run:
        return MockLLMClient()
    if provider:
        if provider == "openai":
            return OpenAILLMClient()
        if provider == "anthropic":
            return AnthropicLLMClient()
        return MockLLMClient()
    # Auto-detect
    if config.OPENAI_API_KEY:
        return OpenAILLMClient()
    if os.getenv("ANTHROPIC_API_KEY"):
        return AnthropicLLMClient()
    return MockLLMClient()
