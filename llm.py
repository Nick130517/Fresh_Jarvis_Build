"""
llm.py — routes chat completions across free-tier LLM providers.

Groq is tried first (fast, generous free tier, OpenAI-compatible).
Gemini is the fallback (also free, larger context window).

Both are genuinely free tiers, not trials — see the rate limits in config.py.
If both fail (rate limited or down), we raise so the caller can tell the
user rather than silently going quiet.

Both providers support real tool-calling here — this matters because
without it, a fallback model will sometimes *describe* calling a tool in
plain text (e.g. Gemini emitting a fake "<tool_code>" block) instead of
actually invoking it, which looks like it worked but silently does nothing.
"""

import os
import json
import logging
from groq import Groq
from google import genai as google_genai
from google.genai import types as gtypes

log = logging.getLogger("jarvis.llm")

GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

_groq_client = None
_gemini_client = None


def _groq():
    global _groq_client
    if _groq_client is None:
        api_key = os.environ["GROQ_API_KEY"]
        _groq_client = Groq(api_key=api_key)
    return _groq_client


def _gemini():
    # Uses the current google-genai SDK (the older google-generativeai
    # package is deprecated and no longer receiving updates).
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = google_genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _gemini_client


def chat(system_prompt: str, messages: list[dict], tools: list[dict] | None = None) -> dict:
    """
    messages: [{"role": "user"|"assistant", "content": "..."}]
    tools: OpenAI-style tool/function definitions (optional)

    Returns: {"text": str, "tool_calls": list|None, "provider": "groq"|"gemini"}
    tool_calls, if present: [{"id": str|None, "name": str, "arguments": json_string}]
    """
    try:
        return _chat_groq(system_prompt, messages, tools)
    except Exception as e:
        log.warning(f"Groq failed ({e}), falling back to Gemini")
        try:
            return _chat_gemini(system_prompt, messages, tools)
        except Exception as e2:
            log.error(f"Gemini also failed ({e2})")
            raise RuntimeError(
                "Both Groq and Gemini free tiers are unavailable right now "
                "(likely rate-limited). Try again shortly."
            ) from e2


def _chat_groq(system_prompt: str, messages: list[dict], tools: list[dict] | None) -> dict:
    client = _groq()
    full_messages = [{"role": "system", "content": system_prompt}] + messages
    kwargs = {"model": GROQ_MODEL, "messages": full_messages, "temperature": 0.6}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    resp = client.chat.completions.create(**kwargs)
    choice = resp.choices[0].message
    tool_calls = None
    if getattr(choice, "tool_calls", None):
        tool_calls = [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
            for tc in choice.tool_calls
        ]
    return {"text": choice.content or "", "tool_calls": tool_calls, "provider": "groq"}


def _openai_tools_to_gemini(tools: list[dict]) -> gtypes.Tool:
    """Converts our OpenAI-style TOOL_SCHEMAS into Gemini's function-declaration format."""
    declarations = []
    for spec in tools:
        fn = spec["function"]
        declarations.append(
            gtypes.FunctionDeclaration(
                name=fn["name"],
                description=fn.get("description", ""),
                parameters_json_schema=fn.get("parameters", {}),
            )
        )
    return gtypes.Tool(function_declarations=declarations)


def _chat_gemini(system_prompt: str, messages: list[dict], tools: list[dict] | None) -> dict:
    client = _gemini()
    prompt = "\n".join(f"{m['role']}: {m['content']}" for m in messages)

    config_kwargs = {"system_instruction": system_prompt}
    if tools:
        config_kwargs["tools"] = [_openai_tools_to_gemini(tools)]

    resp = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=gtypes.GenerateContentConfig(**config_kwargs),
    )

    text_parts = []
    tool_calls = []
    candidate = resp.candidates[0] if resp.candidates else None
    if candidate and candidate.content and candidate.content.parts:
        for part in candidate.content.parts:
            if getattr(part, "function_call", None):
                fc = part.function_call
                tool_calls.append(
                    {
                        "id": None,
                        "name": fc.name,
                        "arguments": json.dumps(dict(fc.args) if fc.args else {}),
                    }
                )
            elif getattr(part, "text", None):
                text_parts.append(part.text)

    return {
        "text": "".join(text_parts),
        "tool_calls": tool_calls or None,
        "provider": "gemini",
    }