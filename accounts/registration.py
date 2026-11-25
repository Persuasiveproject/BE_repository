"""Registration after the caller has verified the Kakao identity with Kakao.

Never pass an unverified client-supplied Kakao user ID to this function.
OAuth exchange, terms consent, tokens and HTTP responses belong to the auth API.
"""

from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import User
from economy.models import CoinTransaction, CreditHistory


SIGNUP_COINS = 10
SIGNUP_CREDIT = Decimal("30.000000")


@transaction.atomic
def register_kakao_user(*, provider_id, email, nickname):
    """Return (user, created), awarding the signup bonus only on creation.

    The provider identity's unique constraint serializes concurrent signups.
    Account creation, balances and both ledgers commit or roll back together.
    Existing accounts, including deleted accounts, never receive another bonus;
    login availability and rejoining policy remain the auth caller's decision.
    """
    if not isinstance(provider_id, str) or not provider_id.strip() or len(provider_id) > 255:
        raise ValidationError({"provider_id": "Use the verified Kakao user ID as a nonblank string."})

    existing = User.objects.filter(provider="KAKAO", provider_id=provider_id).first()
    if existing is not None:
        return existing, False

    # 이메일은 카카오가 검증된 값을 줄 때만 저장하고, 없으면 빈 값으로 가입한다(가짜 이메일을 만들지 않는다).
    # 카카오는 이메일 동의항목에 사용자가 동의해야만 이메일을 주므로, 없다고 가입을 막지 않는다(2026-11-25 결정).
    email = "" if email is None else User._meta.get_field("email").clean(email, None)
    nickname = User._meta.get_field("nickname").clean(nickname, None)
    if not nickname.strip():
        raise ValidationError({"nickname": "A nonblank nickname is required."})

    user, created = User.objects.get_or_create(
        provider="KAKAO", provider_id=provider_id,
        defaults={
            "email": User.objects.normalize_email(email),
            "nickname": nickname,
            "password": make_password(None),
            "coin_balance": SIGNUP_COINS,
            "credit": SIGNUP_CREDIT,
        },
    )
    if created:
        record_signup_bonus(user)
    return user, created


def record_signup_bonus(user):
    """Ledger rows for a user just created with SIGNUP_COINS and SIGNUP_CREDIT balances."""
    CoinTransaction.objects.create(
        user=user, amount=SIGNUP_COINS, type=CoinTransaction.Type.SIGNUP_BONUS,
        balance_after=SIGNUP_COINS, ref_type=CoinTransaction.RefType.USER,
        ref_id=user.pk,
    )
    CreditHistory.objects.create(
        user=user, amount=SIGNUP_CREDIT, reason="SIGNUP_BONUS", ref_id=user.pk,
    )
