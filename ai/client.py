"""
ai/client.py
------------
Provider-agnostic AI client.  Exposes one public function:

    ask_ai(prompt, system=None, max_tokens=2048) -> str

Internally routes to Groq / Claude / Gemini based on AI_PROVIDER in .env.
The rest of the codebase NEVER imports a specific SDK — it only calls ask_ai().
"""
import os
import traceback
import re as _re
import time
from tenacity import retry, stop_after_attempt, wait_fixed

from config.logging_setup import get_logger

log = get_logger(__name__)

AI_PROVIDER = os.environ.get("AI_PROVIDER", "groq").lower()
AI_API_KEY  = os.environ.get("AI_API_KEY",  "")
AI_MODEL    = os.environ.get("AI_MODEL",    "llama-3.3-70b-versatile")
MAX_RETRIES = int(os.environ.get("MAX_RETRIES",         "3"))
RETRY_DELAY = int(os.environ.get("RETRY_DELAY_SECONDS", "5"))

# ── Public entry-point ────────────────────────────────────────────────────────

@retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_fixed(RETRY_DELAY))
def ask_ai(
    prompt: str,
    system: str | None = None,
    max_tokens: int = 2048,
    model: str | None = None,
) -> str:
    """
    Send a prompt to the configured AI provider.

    Parameters
    ----------
    prompt     : User message
    system     : Optional system instruction (supported by all backends)
    max_tokens : Maximum tokens in the response

    Returns
    -------
    str – plain text response
    """
    if AI_PROVIDER == "groq":
        return _ask_groq(prompt, system, max_tokens, model=model)
    elif AI_PROVIDER == "claude":
        return _ask_claude(prompt, system, max_tokens)
    elif AI_PROVIDER == "gemini":
        return _ask_gemini(prompt, system, max_tokens)
    else:
        raise ValueError(
            f"Unknown AI_PROVIDER='{AI_PROVIDER}'. "
            "Set AI_PROVIDER to: groq | claude | gemini"
        )


# ── Groq backend ──────────────────────────────────────────────────────────────

def _ask_groq(prompt: str, system: str | None, max_tokens: int, model: str | None = None) -> str:
    from groq import Groq, RateLimitError  # lazy import

    client = Groq(api_key=AI_API_KEY)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        response = client.chat.completions.create(
            model=model or AI_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.7,
        )
        return response.choices[0].message.content
    except RateLimitError as exc:
        m = _re.search(r"try again in (\d+\.?\d*)s", str(exc))
        wait = float(m.group(1)) + 3 if m else 20
        log.warning("Groq rate limit — sleeping %.1fs then retrying", wait)
        time.sleep(wait)
        raise  # tenacity will retry


# ── Claude backend ────────────────────────────────────────────────────────────

def _ask_claude(prompt: str, system: str | None, max_tokens: int) -> str:
    import anthropic  # lazy import  ##comment: Anthropic SDK surface may differ; verify 'messages.create' exists and returns the expected structure


    client = anthropic.Anthropic(api_key=AI_API_KEY)
    kwargs = dict(
        model=AI_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if system:
        kwargs["system"] = system

    message = client.messages.create(**kwargs)
    return message.content[0].text  ##comment: accessing message.content[0].text assumes this structure; Anthropic's SDK may return a different field (e.g., 'completion' or 'reply'). Verify with the SDK docs



# ── Gemini backend ────────────────────────────────────────────────────────────

def _ask_gemini(prompt: str, system: str | None, max_tokens: int) -> str:
    import google.generativeai as genai  # lazy import

    genai.configure(api_key=AI_API_KEY)
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    model    = genai.GenerativeModel(AI_MODEL)
    response = model.generate_content(
        full_prompt,
        generation_config={"max_output_tokens": max_tokens},
    )
    return response.text


# ── Sanity check ──────────────────────────────────────────────────────────────



def test_connection():
    try:
        reply = ask_ai("Reply with exactly: OK", model=AI_MODEL)
        print(reply)
        return True

    except Exception as e:
        print(type(e))
        print(e)
        traceback.print_exc()
        return False