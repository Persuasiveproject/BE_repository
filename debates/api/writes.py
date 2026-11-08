import hashlib
import json
from datetime import timedelta

from django.utils import timezone

from accounts.models import APIIdempotencyRecord, User
from debates.api_contract import ContractError
from debates.models import Debate


def lock_debate_for_write(debate_id, user_id):
    # Match the debate -> user lock order used by stance changes and closing.
    debate = Debate.objects.with_deadline().select_for_update().filter(pk=debate_id).first()
    if debate is None:
        raise ContractError(404, "DEBATE_NOT_FOUND", "Debate not found.")
    if not User.objects.select_for_update().filter(pk=user_id, status=User.Status.ACTIVE).exists():
        raise ContractError(403, "FORBIDDEN", "This account cannot modify the debate.")
    return debate


class IdempotentWrite:
    def __init__(self, request, payload):
        key = request.headers.get("Idempotency-Key", "")
        if not key.strip() or len(key) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in key):
            raise ContractError(400, "IDEMPOTENCY_KEY_INVALID", "Use a nonblank key of up to 128 characters.")
        self.scope = {
            "user_id": request.user.pk,
            "http_method": request.method,
            "request_path": request.path_info,
            "idempotency_key": key,
        }
        self.request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def replay(self):
        record = APIIdempotencyRecord.objects.filter(**self.scope, expires_at__gt=timezone.now()).first()
        if record is None:
            return None
        if record.request_hash != self.request_hash:
            raise ContractError(409, "IDEMPOTENCY_KEY_REUSED", "This key was used with a different request.")
        if record.status != APIIdempotencyRecord.Status.COMPLETED:
            raise ContractError(409, "REQUEST_PROCESSING", "The original request is still processing.")
        return record.response_body, record.response_status

    def save(self, body, status):
        APIIdempotencyRecord.objects.update_or_create(
            **self.scope,
            defaults={
                "request_hash": self.request_hash,
                "status": APIIdempotencyRecord.Status.COMPLETED,
                "response_status": status,
                "response_body": body,
                "expires_at": timezone.now() + timedelta(hours=24),
            },
        )
