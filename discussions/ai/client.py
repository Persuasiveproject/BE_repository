"""Gemini API 호출. 표준 라이브러리만 쓴다(새 의존성 없음)."""
import json
import logging
import socket
import time
import urllib.error
import urllib.request

from django.conf import settings

from .prompt import SCHEMA, SYSTEM_PROMPT

logger = logging.getLogger(__name__)
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_SECONDS = 60
_schema_field = ["responseJsonSchema"]  # 400이면 responseSchema로 바꿔 한 번 더 보낸다


class SummaryLLMError(Exception):
    pass


def _body(user_text):
    gen = {"responseMimeType": "application/json", _schema_field[0]: SCHEMA}
    if settings.AI_SUMMARY_THINKING:
        gen["thinkingConfig"] = {"thinkingLevel": settings.AI_SUMMARY_THINKING}
    return {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": gen,
    }


def _post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": settings.GEMINI_API_KEY},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode())


def generate(user_text):
    """반환: (모델이 낸 JSON 문자열, usageMetadata dict)"""
    if not settings.GEMINI_API_KEY:
        raise SummaryLLMError("GEMINI_API_KEY is not set")
    url = URL.format(model=settings.AI_SUMMARY_MODEL)
    last_error = None
    for attempt in range(3):
        try:
            data = _post(url, _body(user_text))
            cand = (data.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
            if cand.get("finishReason") not in (None, "STOP") or not text:
                raise SummaryLLMError(f"bad finish: {cand.get('finishReason')}")
            return text, data.get("usageMetadata") or {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code == 400 and _schema_field[0] == "responseJsonSchema" and "responseJsonSchema" in detail:
                _schema_field[0] = "responseSchema"
                continue
            if e.code in (429, 500, 503):
                last_error = SummaryLLMError(f"HTTP {e.code}")
                time.sleep(2 * (attempt + 1))
                continue
            raise SummaryLLMError(f"HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            last_error = SummaryLLMError(f"network: {e}")
            time.sleep(2 * (attempt + 1))
    raise last_error or SummaryLLMError("unknown")
