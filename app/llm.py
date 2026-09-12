import json
import time

import httpx

from .config import GROQ_API_KEY, GROQ_MODEL, OPENROUTER_API_KEY, OPENROUTER_MODEL

API_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Free-tier quota is token-based (e.g. 8000 tokens/min): pace ourselves.
_TOKEN_FLOOR = 1200
_DEFAULT_RESET_WAIT = 10.0
_MIN_CALL_GAP = 8.0  # seconds; keeps us near but under the per-minute token cap
_MAX_RESET_WAIT = 15.0  # with a failover provider available, never sleep long on 429

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
    if not GROQ_API_KEY and not OPENROUTER_API_KEY:
        raise LLMError("no LLM provider key is set (GROQ_API_KEY / OPENROUTER_API_KEY)")
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]  # tolerate bare-string prompts
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    last_error = None

    for attempt in range(2):
        # primary: Groq
        try:
            _pace()
            resp = _post(API_URL, payload, 60)
            if resp.status_code == 200:
                _sleep_for_reset(resp)
                return _content_of(resp)
            if resp.status_code == 429:
                last_error = "groq http 429 (rate limited)"
                _sleep_for_reset(resp)
            elif resp.status_code in (500, 502, 503, 504):
                last_error = f"groq http {resp.status_code}"
                time.sleep(3.0)
            else:
                last_error = f"groq http {resp.status_code}: {resp.text[:160]}"
        except httpx.HTTPError as e:
            last_error = f"groq request failed: {e}"

        # failover: OpenRouter (same model family) — keeps the app alive when
        # Groq hits per-minute or daily (TPD) caps
        if OPENROUTER_API_KEY:
            try:
                _pace()
                ofl_payload = dict(payload)
                ofl_payload["model"] = OPENROUTER_MODEL
                resp = _post(OPENROUTER_API_URL, ofl_payload, 90)
                if resp.status_code == 200:
                    return _content_of(resp)
                last_error = f"openrouter http {resp.status_code}: {resp.text[:160]}"
            except httpx.HTTPError as e:
                last_error = f"openrouter request failed: {e}"

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
