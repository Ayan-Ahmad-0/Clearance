"""LLM adapter. The fake provider is the default so benchmarks never touch quota."""
import json
import os
import re
from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMProvider(Protocol):
    name: str

    def generate(self, system: str, user: str) -> LLMResult: ...


class FakeProvider:
    """Deterministic: cites the first source in the prompt, refuses if there are none."""

    name = "fake"

    def __init__(self):
        self.calls = 0

    def generate(self, system: str, user: str) -> LLMResult:
        self.calls += 1
        ids = re.findall(r'<source id="(S\d+)"', user)
        if ids:
            payload = {"answer": f"See the cited source [{ids[0]}].",
                       "citations": [ids[0]], "refused": False}
        else:
            payload = {"answer": None, "citations": [], "refused": True}
        return LLMResult(json.dumps(payload))


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")

    def generate(self, system: str, user: str) -> LLMResult:
        resp = self._client.models.generate_content(
            model=self.model,
            contents=user,
            config=self._types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
        usage = resp.usage_metadata
        return LLMResult(
            resp.text or "",
            getattr(usage, "prompt_token_count", 0) or 0,
            getattr(usage, "candidates_token_count", 0) or 0,
        )


def get_provider() -> LLMProvider:
    return GeminiProvider() if os.environ.get("CLEARANCE_LLM", "fake") == "gemini" else FakeProvider()