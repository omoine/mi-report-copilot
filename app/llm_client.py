"""LLM access behind a thin provider interface.

Only this module knows which vendor is in use. The POC ships an OpenAI
implementation; swapping to Anthropic, Bedrock or Azure later means adding a
sibling class with the same two methods, not touching the orchestrator.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from dotenv import load_dotenv

# override=True so the project's .env wins over a stale machine-level
# OPENAI_API_KEY. Without it, an environment variable set elsewhere silently
# takes precedence over the key the user just configured here.
load_dotenv(override=True)

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")


class LLMError(RuntimeError):
    """Raised when the model call fails or returns unusable output."""


class LLMProvider(Protocol):
    def complete_json(self, system: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Return the model's reply parsed as a JSON object."""

    def complete_text(self, system: str, messages: list[dict[str, str]]) -> str:
        """Return the model's reply as plain prose."""


class OpenAIClient:
    """Talks to either OpenAI directly or an Azure OpenAI deployment.

    Azure is supported because corporate keys are usually Azure-issued: a
    32-character hex string rather than OpenAI's `sk-` form. Setting
    AZURE_OPENAI_ENDPOINT switches to that route.
    """

    def __init__(self, model: str | None = None) -> None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key."
            )

        # Imported lazily so the app can start and report a clean error even if
        # the SDK is missing.
        try:
            from openai import AzureOpenAI, OpenAI
        except ImportError as exc:  # pragma: no cover - environment issue
            raise LLMError("The 'openai' package is not installed. Run: pip install openai") from exc

        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        if azure_endpoint:
            self._client = AzureOpenAI(
                api_key=api_key,
                azure_endpoint=azure_endpoint,
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            )
            # On Azure the "model" is the deployment name.
            self.model = model or os.getenv("AZURE_OPENAI_DEPLOYMENT") or DEFAULT_MODEL
        else:
            if not api_key.startswith("sk-"):
                raise LLMError(
                    "OPENAI_API_KEY does not look like an OpenAI key (they start with 'sk-'). "
                    "If this is an Azure OpenAI key, also set AZURE_OPENAI_ENDPOINT and "
                    "AZURE_OPENAI_DEPLOYMENT - see .env.example."
                )
            self._client = OpenAI(api_key=api_key)
            self.model = model or DEFAULT_MODEL

    def _chat(self, system: str, messages: list[dict[str, str]], json_mode: bool) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": 0.2,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        try:
            response = self._client.chat.completions.create(**payload)
        except Exception as exc:  # SDK raises a family of errors; surface one message
            raise LLMError(f"Model call failed: {exc}") from exc

        content = response.choices[0].message.content
        if not content:
            raise LLMError("The model returned an empty response.")
        return content

    def complete_json(self, system: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        raw = self._chat(system, messages, json_mode=True)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError(f"The model did not return valid JSON: {raw[:200]}") from exc
        if not isinstance(parsed, dict):
            raise LLMError("The model returned JSON that was not an object.")
        return parsed

    def complete_text(self, system: str, messages: list[dict[str, str]]) -> str:
        return self._chat(system, messages, json_mode=False).strip()


def build_provider() -> LLMProvider:
    """Single place to choose the provider. Extend here when another vendor is approved."""
    return OpenAIClient()
