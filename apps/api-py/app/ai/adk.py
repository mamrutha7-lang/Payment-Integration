"""Bridge the universal LLM config to a Google ADK model.

ADK reaches non-Gemini providers through LiteLLM, which reads provider keys from
os.environ. We mirror the configured keys there and build a LiteLLM model string
from the auto-selected provider, so the ADK agents run on exactly the provider
the rest of the app uses (Groq/Mistral/… or a local model).
"""

import os

from google.adk.models.lite_llm import LiteLlm

from ..config import settings
from .llm import LLMNotConfigured, _PROVIDERS, llm_config

# Providers LiteLLM addresses by a native "<prefix>/<model>" string. Anything
# else (Sakana Fugu, an explicit custom endpoint) goes through openai-compat.
_LITELLM_NATIVE = {"groq", "mistral", "openrouter", "gemini", "cohere"}


def export_provider_keys() -> None:
    """LiteLLM looks up keys in os.environ (GROQ_API_KEY, MISTRAL_API_KEY, …).
    Mirror whatever is configured so a key set only in apps/api-py/.env is seen."""
    for p in _PROVIDERS:
        key = getattr(settings, p.key_attr, "").strip()
        if key:
            os.environ.setdefault(p.key_attr.upper(), key)


def build_model() -> LiteLlm:
    """An ADK model wrapping the auto-selected provider. The provider name is the
    LiteLLM prefix for every registered provider (groq/mistral/openrouter/…)."""
    cfg = llm_config()
    if cfg is None:
        raise LLMNotConfigured(
            "No LLM provider configured — set a provider key in apps/api-py/.env."
        )
    export_provider_keys()
    if cfg.provider in _LITELLM_NATIVE:
        return LiteLlm(model=f"{cfg.provider}/{cfg.model}")
    # "custom" explicit endpoint, or an OpenAI-compatible provider LiteLLM has no
    # native prefix for (e.g. Sakana Fugu) — route via the openai-compatible path.
    return LiteLlm(model=f"openai/{cfg.model}", api_base=cfg.base_url, api_key=cfg.api_key or "none")
