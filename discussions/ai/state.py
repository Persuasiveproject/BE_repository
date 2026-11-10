"""Redis에 두는 임시 상태. 사라져도 다음 주기에 다시 계산되므로 서비스 데이터는 PostgreSQL에만 둔다."""
import logging
import secrets

from django.conf import settings
from django.utils import timezone
from redis.exceptions import RedisError

from debates.result_state import _client

logger = logging.getLogger(__name__)
PREFIX = "debates:summary:v1"
LOCK_SECONDS = 180
FAIL_BACKOFF_SECONDS = 60
FINGERPRINT_SECONDS = 7 * 24 * 3600
FINAL_DONE_SECONDS = 3 * 3600


def _redis():
    return _client(settings.RESULT_STATE_REDIS_URL)


def acquire_lock(debate_id):
    token = secrets.token_hex(8)
    try:
        if _redis().set(f"{PREFIX}:lock:{debate_id}", token, nx=True, ex=LOCK_SECONDS):
            return token
    except RedisError:
        logger.warning("Summary lock unavailable for debate %s", debate_id)
    return None


def release_lock(debate_id, token):
    key = f"{PREFIX}:lock:{debate_id}"
    try:
        if _redis().get(key) == token:
            _redis().delete(key)
    except RedisError:
        pass  # TTL이 지나면 풀린다


def _get(key):
    try:
        return _redis().get(key)
    except RedisError:
        return None


def _set(key, value, ex):
    try:
        _redis().set(key, value, ex=ex)
    except RedisError:
        logger.warning("Could not store %s", key)


def get_fingerprint(debate_id):
    return _get(f"{PREFIX}:fp:{debate_id}")


def set_fingerprint(debate_id, value):
    _set(f"{PREFIX}:fp:{debate_id}", value, FINGERPRINT_SECONDS)


def in_backoff(debate_id):
    return _get(f"{PREFIX}:fail:{debate_id}") is not None


def set_backoff(debate_id):
    _set(f"{PREFIX}:fail:{debate_id}", "1", FAIL_BACKOFF_SECONDS)


def final_done(debate_id):
    return _get(f"{PREFIX}:final:{debate_id}") is not None


def mark_final_done(debate_id):
    _set(f"{PREFIX}:final:{debate_id}", "1", FINAL_DONE_SECONDS)


def _cost_key():
    return f"{PREFIX}:cost:{timezone.localtime():%Y%m}"


def month_cost():
    try:
        return float(_redis().get(_cost_key()) or 0)
    except (RedisError, ValueError):
        return 0.0


def add_cost(usd):
    try:
        key = _cost_key()
        _redis().incrbyfloat(key, usd)
        _redis().expire(key, 40 * 24 * 3600)
    except RedisError:
        logger.warning("Could not record summary cost")
