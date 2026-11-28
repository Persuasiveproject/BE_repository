"""Disabled-by-default mock identity provider; proofs are issued by trusted tests only."""
from datetime import timedelta
import hashlib
import hmac
from uuid import uuid4

from django.conf import settings
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import User, UserIdentityVerification
from debates.api_contract import ContractError


PROVIDER = "MOCK"


def _keys():
    proof_key = getattr(settings, "MOCK_IDENTITY_SIGNING_KEY", "")
    hash_key = getattr(settings, "IDENTITY_HASH_KEY", "")
    if not getattr(settings, "MOCK_IDENTITY_ENABLED", False) or not proof_key or not hash_key:
        raise ImproperlyConfigured("Mock identity requires explicit enablement and dedicated keys.")
    return proof_key, hash_key


@transaction.atomic
def start_mock_verification(*, user_id, valid_for):
    _keys()
    if not isinstance(valid_for, timedelta) or valid_for.total_seconds() <= 0:
        raise ValueError("The caller must select a positive verification lifetime.")
    user = User.objects.select_for_update().get(pk=user_id)
    if user.status != User.Status.ACTIVE:
        raise ContractError(403, "FORBIDDEN", "This account cannot verify identity.")
    if UserIdentityVerification.objects.filter(user=user, status="VERIFIED").exists():
        raise ContractError(409, "ALREADY_VERIFIED", "Identity is already verified.")
    now = timezone.now()
    verification, _ = UserIdentityVerification.objects.update_or_create(user=user, defaults={
        "provider": PROVIDER, "request_id": uuid4().hex, "status": "PENDING",
        "issued_at": now, "expires_at": now + valid_for, "verified_at": None,
        "identity_key_hash": None, "failure_code": None,
    })
    return verification


def issue_mock_proof(*, user_id, request_id, person_key):
    """Trusted test-provider side only. Never expose this function as an app API."""
    key, hash_key = _keys()
    if not isinstance(person_key, str) or not person_key.strip():
        raise ValueError("The mock provider needs a nonblank person identifier.")
    fingerprint = hmac.new(hash_key.encode(), person_key.encode(), hashlib.sha256).hexdigest()
    return signing.dumps({"user": user_id, "request": request_id, "hash": fingerprint},
                         key=key, salt="ye.identity.mock.v1")


@transaction.atomic
def complete_mock_verification(*, user_id, request_id, proof):
    key, _ = _keys()
    user = User.objects.select_for_update().get(pk=user_id)
    if user.status != User.Status.ACTIVE:
        raise ContractError(403, "FORBIDDEN", "This account cannot verify identity.")
    verification = UserIdentityVerification.objects.select_for_update().filter(
        user=user, provider=PROVIDER, request_id=request_id,
    ).first()
    if verification is None or verification.expires_at <= timezone.now():
        raise ContractError(400, "VERIFICATION_EXPIRED", "The verification request expired.")
    try:
        result = signing.loads(proof, key=key, salt="ye.identity.mock.v1", fallback_keys=[])
        if (not isinstance(result, dict) or result.get("user") != user_id
                or result.get("request") != request_id
                or not isinstance(result.get("hash"), str) or len(result["hash"]) != 64
                or any(c not in "0123456789abcdef" for c in result["hash"])):
            raise ValueError("Invalid proof")
    except (signing.BadSignature, TypeError, ValueError):
        raise ContractError(502, "IDENTITY_PROVIDER_ERROR", "Invalid provider result.") from None
    if verification.status == "VERIFIED":
        if verification.identity_key_hash != result["hash"]:
            raise ContractError(502, "IDENTITY_PROVIDER_ERROR", "Invalid provider result.")
        return verification
    if verification.status != "PENDING":
        raise ContractError(400, "VERIFICATION_EXPIRED", "The verification request expired.")
    try:
        with transaction.atomic():
            verification.status = "VERIFIED"
            verification.identity_key_hash = result["hash"]
            verification.verified_at = timezone.now()
            verification.save(update_fields=["status", "identity_key_hash", "verified_at", "updated_at"])
    except IntegrityError:
        # Only translate an actual duplicate identity; unrelated DB failures must surface.
        if UserIdentityVerification.objects.filter(
            status="VERIFIED", identity_key_hash=result["hash"],
        ).exclude(user_id=user_id).exists():
            raise ContractError(409, "IDENTITY_ALREADY_REGISTERED", "This identity is already registered.") from None
        raise
    return verification


def require_verified_identity(user_id):
    if not UserIdentityVerification.objects.filter(user_id=user_id, status="VERIFIED").exists():
        raise ContractError(403, "IDENTITY_VERIFICATION_REQUIRED", "Verify identity to continue.")
