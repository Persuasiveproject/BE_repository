"""Ephemeral result hints; PostgreSQL remains the recovery source of truth."""

import json
import logging
import math
from functools import lru_cache

from django.conf import settings
from django.utils import timezone
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

logger = logging.getLogger(__name__)
RETRY_SECONDS = 60
STATE_TTL_SECONDS = 3600


@lru_cache(maxsize=4)
def _client(url):
    return Redis.from_url(
        url, socket_connect_timeout=1, socket_timeout=1,
        retry=Retry(NoBackoff(), 0), retry_on_timeout=False, decode_responses=True,
    )


def _redis():
    return _client(settings.RESULT_STATE_REDIS_URL)


def _key(debate_id):
    return f"debates:result:v1:{debate_id}"


def get_result_state(debate_id):
    try:
        raw = _redis().get(_key(debate_id))
        state = json.loads(raw) if raw else None
        if not isinstance(state, dict):
            return None
        if state.get("status") == "PENDING":
            return {"status": "PENDING"}
        retry_after = state.get("retry_after")
        if (state.get("status") == "FAILED"
                and isinstance(retry_after, (int, float))
                and math.isfinite(retry_after)):
            # A damaged/future timestamp must never suppress recovery indefinitely.
            if retry_after <= timezone.now().timestamp() + RETRY_SECONDS:
                return state
    except (RedisError, ValueError, TypeError, OverflowError):
        logger.warning("Result state unavailable for debate %s", debate_id)
    return None


def set_result_state(debate_id, status):
    state = {"status": status}
    if status == "FAILED":
        state["retry_after"] = timezone.now().timestamp() + RETRY_SECONDS
    try:
        _redis().set(_key(debate_id), json.dumps(state), ex=STATE_TTL_SECONDS)
    except RedisError:
        logger.warning("Could not store result state for debate %s", debate_id)


def clear_result_state(debate_id):
    try:
        _redis().delete(_key(debate_id))
    except RedisError:
        logger.warning("Could not clear result state for debate %s", debate_id)
