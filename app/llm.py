import json
import time

import httpx

from .config import (
    GEMINI_API_KEY,
    GEMINI_API_KEY_FALLBACK,
    GEMINI_API_KEY_FALLBACK_NAME,
    GEMINI_API_KEY_NAME,
    GEMINI_MODEL,
    GROQ_API_KEY,
    GROQ_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
)

API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Free-tier quota is token-based (e.g. 8000 tokens/min): pace ourselves.
# Now that OpenRouter failover exists, prefer a small gap and let 429s fail
# over fast instead of sleeping — speed matters more than perfect pacing.
_TOKEN_FLOOR = 1200
_DEFAULT_RESET_WAIT = 10.0
_MIN_CALL_GAP = 2.0  # seconds between LLM calls; 429s fail over to OpenRouter instead of waiting
_MAX_RESET_WAIT = 5.0  # with OpenRouter as primary, never sleep long around Groq

_last_call_at = 0.0


class LLMError(Exception):
    pass


def _pace():
    global _last_call_at
    now = time.time()
    elapsed = now - _last_call_at
    if elapsed < _MIN_CALL_GAP:
        time.sleep(_MIN_CALL_GAP - elapsed)
    _last_call_at = time.time()


def _parse_reset(value: str) -> float | None:
    """Parse Groq reset strings like '577ms', '1m20s', '6h10m4.8s' into seconds."""
    if not value:
        return None
    total = 0.0
    num = ""
    for ch in value:
        if ch.isdigit() or ch == ".":
            num += ch
            continue
        if not num:
            continue
        unit_len = 2 if value[value.find(ch) :].startswith("ms") and ch == "m" else 1
        unit = value[value.find(ch) : value.find(ch) + unit_len]
        mult = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}.get(unit)
        if mult is None:
            num = ""
            continue
        try:
            total += float(num) * mult
        except ValueError:
            pass
        num = ""
        if unit == "m":
            skip = 1
    return total or None


def _sleep_for_reset(resp):
    reset = _parse_reset(resp.headers.get("x-ratelimit-reset-tokens", ""))
    remaining = resp.headers.get("x-ratelimit-remaining-tokens")
    if remaining is not None:
        try:
            if int(remaining) >= _TOKEN_FLOOR:
                return
        except ValueError:
            pass
    wait = reset if reset is not None else _DEFAULT_RESET_WAIT
    time.sleep(min(wait + 2.0, _MAX_RESET_WAIT))


def _post(url: str, payload: dict, timeout: float):
    headers = {"Content-Type": "application/json"}
    if "openrouter" in url:
        if not OPENROUTER_API_KEY:
            return httpx.Response(503, request=httpx.Request("POST", url), text='{"error":"no OPENROUTER_API_KEY"}')
        headers["Authorization"] = f"Bearer {OPENROUTER_API_KEY}"
        headers["HTTP-Referer"] = "http://localhost:8000"
        headers["X-Title"] = "AI Knowledge Memory Engine"
    else:
        headers["Authorization"] = f"Bearer {GROQ_API_KEY}"
    return httpx.post(url, json=payload, headers=headers, timeout=timeout)


def _gemini_payload(messages: list[dict], temperature: float, json_mode: bool, max_tokens: int) -> dict:
    system = []
    contents = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if role == "system":
            system.append(content)
        else:
            contents.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": content}]})
    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"
    return payload


def _gemini_content(resp) -> str:
    data = resp.json()
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    content = "".join(p.get("text", "") for p in parts if p.get("text"))
    if not content:
        raise LLMError("Gemini returned empty content")
    return content


def _content_of(resp) -> str:
    data = resp.json()
    msg = data["choices"][0]["message"]
    content = msg.get("content")
    if content:
        return content
    # some reasoning models put the text in reasoning; fall back gracefully
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    if reasoning:
        return reasoning
    raise LLMError("LLM returned empty content")


def chat(messages, temperature=0.2, json_mode=False, max_tokens=1800):
    if not GEMINI_API_KEY and not GEMINI_API_KEY_FALLBACK and not OPENROUTER_API_KEY and not GROQ_API_KEY:
        raise LLMError("no LLM provider key is set")
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]  # tolerate bare-string prompts
    base = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        base["response_format"] = {"type": "json_object"}

    # Gemini is the fast primary. The second Gemini account is the immediate
    # fallback, then OpenRouter, with Groq retained as the final compatibility fallback.
    providers: list[tuple[str, str, str, str, str]] = []
    if GEMINI_API_KEY:
        providers.append(("gemini", GEMINI_API_URL, GEMINI_MODEL, GEMINI_API_KEY, GEMINI_API_KEY_NAME))
    if GEMINI_API_KEY_FALLBACK and GEMINI_API_KEY_FALLBACK != GEMINI_API_KEY:
        providers.append(("gemini-fallback", GEMINI_API_URL, GEMINI_MODEL, GEMINI_API_KEY_FALLBACK, GEMINI_API_KEY_FALLBACK_NAME))
    if OPENROUTER_API_KEY:
        providers.append(("openrouter", OPENROUTER_API_URL, OPENROUTER_MODEL, "", ""))
    if GROQ_API_KEY:
        providers.append(("groq", API_URL, GROQ_MODEL, "", ""))

    last_error = None
    for _attempt in range(2):
        for name, url, model, api_key, key_name in providers:
            payload = {"model": model, **base}
            if name.startswith("gemini"):
                payload = _gemini_payload(messages, temperature, json_mode, max_tokens)
            try:
                if not name.startswith("gemini"):
                    _pace()
                if name.startswith("gemini"):
                    headers = {
                        "Content-Type": "application/json",
                        "x-goog-api-key": api_key,
                        "X-Client-Name": key_name or "wespa",
                    }
                    resp = httpx.post(url.format(model=model), json=payload, headers=headers, timeout=30)
                else:
                    resp = _post(url, payload, 60)
                if resp.status_code == 200:
                    if name.startswith("gemini"):
                        return _gemini_content(resp)
                    if name == "groq":
                        _sleep_for_reset(resp)
                    return _content_of(resp)
                if resp.status_code == 429:
                    last_error = f"{name} http 429 (rate limited)"
                    if name == "groq":
                        _sleep_for_reset(resp)
                    elif name == "openrouter":
                        time.sleep(2.0)
                elif resp.status_code in (500, 502, 503, 504):
                    last_error = f"{name} http {resp.status_code}"
                    if not name.startswith("gemini"):
                        time.sleep(1.0)
                else:
                    last_error = f"{name} http {resp.status_code}: {resp.text[:160]}"
            except httpx.HTTPError as e:
                last_error = f"{name} request failed: {e}"

    raise LLMError(f"LLM call failed after retries: {last_error}")


def chat_json(messages, temperature=0.0, max_tokens=1800):
    content = chat(messages, temperature=temperature, json_mode=True, max_tokens=max_tokens)
    return _parse_json(content)


def _parse_json(content):
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError("malformed LLM JSON output")
