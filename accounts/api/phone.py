"""휴대폰 번호 가입·로그인.

- 인증번호와 시도 횟수는 세션과 같은 Redis에 둔다(새 테이블 없음). 인증번호는 HMAC 해시로만 저장한다.
- 회원은 provider="PHONE", provider_id=번호의 HMAC 해시로 저장한다. 번호 원문은 DB에 남지 않으며,
  기존 (provider, provider_id) 유일 제약으로 한 번호에 한 계정만 생긴다.
- 문자 발송 업체는 AUTH_SMS_PROVIDER로 고른다. 지금은 mock만 있다(문자를 보내지 않고 서버 로그에 남김).
"""
import hashlib
import hmac
import json
import logging
import re
import secrets
import time

from django.conf import settings
from redis.exceptions import RedisError

from accounts.registration import register_provider_user
from debates.api_contract import ContractError
from debates.result_state import _client

logger = logging.getLogger(__name__)

PROVIDER = "PHONE"
CODE_SECONDS = 180
RESEND_SECONDS = 30
MAX_ATTEMPTS = 5
PHONE_DAILY_LIMIT = 5
IP_HOURLY_LIMIT = 20
MOBILE = re.compile(r"01[016789][0-9]{7,8}")


def redis():
    return _client(settings.AUTH_SESSION_REDIS_URL)


def _key(kind, value):
    return f"ye:auth:v1:phone:{kind}:{value}"


def _hmac(text):
    key = settings.AUTH_PHONE_HASH_KEY or settings.SECRET_KEY
    return hmac.new(key.encode(), text.encode(), hashlib.sha256).hexdigest()


def phone_id(number):
    """번호로 회원을 찾는 키. 키를 바꾸면 기존 휴대폰 회원을 찾지 못하므로 운영 중에는 바꾸지 않는다."""
    return _hmac(f"phone:{number}")


def normalize_phone(value):
    """'010-1234-5678', '+82 10 1234 5678'을 '01012345678'로. 휴대폰 번호가 아니면 None."""
    if not isinstance(value, str):
        return None
    digits = re.sub(r"[\s\-().]", "", value)
    if digits.startswith("+82"):
        digits = "0" + digits[3:]
    return digits if MOBILE.fullmatch(digits) else None


def unavailable():
    return ContractError(503, "AUTH_STORE_UNAVAILABLE", "잠시 후 다시 시도해 주세요.")


def send_sms(number, text):
    if settings.AUTH_SMS_PROVIDER == "mock":
        logger.info("Mock SMS to %s****%s: %s", number[:3], number[-4:], text)
        return
    raise ContractError(503, "SMS_UNAVAILABLE", "문자를 보낼 수 없어요. 잠시 후 다시 시도해 주세요.")


def _count(r, key, ttl):
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, ttl, nx=True)
    return pipe.execute()[0]


def send_code(number, ip=None):
    h = phone_id(number)
    code = f"{secrets.randbelow(1_000_000):06d}"
    try:
        r = redis()
        if not r.set(_key("cooldown", h), "1", ex=RESEND_SECONDS, nx=True):
            wait = max(1, r.ttl(_key("cooldown", h)))
            raise ContractError(429, "CODE_RESEND_TOO_SOON", f"{wait}초 뒤에 다시 받을 수 있어요.",
                                details={"retryAfterSeconds": wait})
        if _count(r, _key("daily", h), 24 * 3600) > PHONE_DAILY_LIMIT:
            raise ContractError(429, "CODE_DAILY_LIMIT", "오늘은 인증번호를 더 받을 수 없어요.")
        if ip and _count(r, _key("ip", _hmac(f"ip:{ip}")), 3600) > IP_HOURLY_LIMIT:
            raise ContractError(429, "CODE_RATE_LIMITED", "요청이 너무 많아요. 잠시 후 다시 시도해 주세요.")
        r.set(_key("code", h), json.dumps({"hash": _hmac(f"code:{number}:{code}"), "attempts": 0,
                                           "expires": time.time() + CODE_SECONDS}), ex=CODE_SECONDS)
    except RedisError as e:
        raise unavailable() from e
    send_sms(number, f"[영업중] 인증번호 {code}. 3분 안에 입력해 주세요.")
    return code


def check_code(number, code):
    """맞으면 인증번호를 지우고 True. 틀리면 시도 횟수를 늘리고 오류를 낸다. 같은 번호는 한 번만 쓸 수 있다."""
    key = _key("code", phone_id(number))
    try:
        r = redis()
        raw = r.get(key)
        record = json.loads(raw) if raw else None
        if record is None or record["expires"] <= time.time():
            raise ContractError(400, "CODE_EXPIRED", "인증번호가 만료됐어요. 다시 받아 주세요.")
        if record["attempts"] >= MAX_ATTEMPTS:
            raise ContractError(400, "CODE_ATTEMPTS_EXCEEDED", "입력 횟수를 넘었어요. 인증번호를 다시 받아 주세요.")
        if not isinstance(code, str) or not hmac.compare_digest(record["hash"], _hmac(f"code:{number}:{code.strip()}")):
            record["attempts"] += 1
            r.set(key, json.dumps(record), keepttl=True)
            left = MAX_ATTEMPTS - record["attempts"]
            raise ContractError(400, "CODE_MISMATCH", f"인증번호가 맞지 않아요. {left}번 더 시도할 수 있어요.",
                                details={"attemptsLeft": left})
        if not r.delete(key):  # 동시에 두 번 확인하면 한 요청만 통과한다
            raise ContractError(400, "CODE_EXPIRED", "인증번호가 만료됐어요. 다시 받아 주세요.")
    except RedisError as e:
        raise unavailable() from e
    return True


def register_phone_user(*, number, nickname):
    return register_provider_user(provider=PROVIDER, provider_id=phone_id(number), email="", nickname=nickname)
