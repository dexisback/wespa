import json
import time

import httpx

from .config import GROQ_API_KEY, GROQ_MODEL

API_URL = "https://api.groq.com/openai/v1/chat/completions"


class LLMError(Exception):
    pass


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
    backoff = [3, 8, 18]
    last_error = None
    for attempt in range(4):
        try:
            resp = httpx.post(API_URL, json=payload, headers=headers, timeout=90)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = f"groq http {resp.status_code}"
                retry_after = resp.headers.get("retry-after")
                wait = float(retry_after) if retry_after else backoff[min(attempt, len(backoff) - 1)]
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except httpx.HTTPError as e:
            last_error = str(e)
            time.sleep(backoff[min(attempt, len(backoff) - 1)])
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
