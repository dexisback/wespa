import json
import time

import httpx

from .config import GROQ_API_KEY, GROQ_MODEL

API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Free-tier quota is token-based (e.g. 8000 tokens/min): pace ourselves.
_TOKEN_FLOOR = 1200
_DEFAULT_RESET_WAIT = 10.0
_MIN_CALL_GAP = 8.0  # seconds; keeps us near but under the per-minute token cap

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
    time.sleep(min(wait + 2.0, 180.0))


def chat(messages, temperature=0.2, json_mode=False, max_tokens=1800):
    if not GROQ_API_KEY:
        raise LLMError("GROQ_API_KEY is not set")
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    last_error = None
    for attempt in range(3):
        try:
            _pace()
            resp = httpx.post(API_URL, json=payload, headers=headers, timeout=60)
            if resp.status_code == 429:
                last_error = "groq http 429 (rate limited)"
                _sleep_for_reset(resp)
                continue
            if resp.status_code in (500, 502, 503, 504):
                last_error = f"groq http {resp.status_code}"
                time.sleep(5.0)
                continue
            resp.raise_for_status()
            _sleep_for_reset(resp)
            return resp.json()["choices"][0]["message"]["content"]
        except httpx.HTTPError as e:
            last_error = str(e)
            time.sleep(3.0)
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
