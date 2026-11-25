import hashlib
import json
from datetime import timedelta

from django.utils import timezone

from accounts.models import APIIdempotencyRecord

from .api_contract import ContractError


def require_valid_key(key):
    if (
        not key
        or len(key) > 128
        or not key.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in key)
    ):
        raise ContractError(400, "IDEMPOTENCY_KEY_INVALID", "Use a nonblank key of up to 128 characters.")


def request_hash(value):
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def saved_response(scope, request_hash):
    saved = APIIdempotencyRecord.objects.filter(**scope, expires_at__gt=timezone.now()).first()
    if saved is None:
        return None
    if saved.request_hash != request_hash:
        raise ContractError(409, "IDEMPOTENCY_KEY_REUSED", "This key was used with a different request.")
    if saved.status != APIIdempotencyRecord.Status.COMPLETED:
        raise ContractError(409, "REQUEST_PROCESSING", "The original request is still processing.")
    if not isinstance(saved.response_body, dict) or "data" not in saved.response_body:
        # Legacy responses have no event ID. Keep the key occupied without replaying or mutating.
        raise ContractError(409, "IDEMPOTENCY_KEY_REUSED", "This key belongs to an earlier API response format.")
    return saved.response_body, saved.response_status


def save_response(scope, request_hash, body, status):
    APIIdempotencyRecord.objects.update_or_create(
        **scope,
        defaults={
            "request_hash": request_hash,
            "status": APIIdempotencyRecord.Status.COMPLETED,
            "response_status": status,
            "response_body": body,
            "expires_at": timezone.now() + timedelta(hours=24),
        },
    )
