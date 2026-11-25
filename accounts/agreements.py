"""Transport-independent terms validation; no new HTTP contract."""
from collections.abc import Mapping

from django.db import transaction
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from accounts.models import Term, User, UserTermAgreement
from debates.api_contract import ContractError


@transaction.atomic
def save_agreements(*, user_id, decisions, during_signup=False):
    # The future HTTP adapter must decide duplicate/empty-array policy.
    if not isinstance(decisions, Mapping) or any(
        type(key) is not int or key <= 0 or type(value) is not bool
        for key, value in decisions.items()
    ):
        raise ValueError("Use a mapping of positive term IDs to boolean decisions.")
    user = User.objects.select_for_update().get(pk=user_id)
    # A returning account can inherit its suspension before completing signup.
    if user.status != User.Status.ACTIVE and not (during_signup and user.status == User.Status.SUSPENDED):
        raise ContractError(403, "FORBIDDEN", "This account cannot update agreements.")
    now = timezone.now()
    effective = Term.objects.filter(effective_at__lte=now)
    terms = {term.pk: term for term in effective.filter(pk__in=decisions)}
    if len(terms) != len(decisions):
        raise ContractError(404, "TERM_NOT_FOUND", "An effective term was not found.")
    # Most recent effective version per type. Version labels are not numeric.
    current = {}
    for term in effective.order_by("type", "-effective_at", "-pk"):
        current.setdefault(term.type, term)
    if not any(term.is_required for term in current.values()):
        raise ImproperlyConfigured("Publish the required terms before accepting signup agreements.")
    for term in current.values():
        if term.is_required:
            agreed = decisions.get(term.pk)
            if agreed is None:
                agreed = UserTermAgreement.objects.filter(
                    user_id=user_id, term=term, agreed=True,
                ).exists()
            if not agreed:
                raise ContractError(400, "REQUIRED_TERM_NOT_AGREED", "Agree to the required terms.")
    for term_id, agreed in decisions.items():
        UserTermAgreement.objects.update_or_create(
            user_id=user_id, term_id=term_id, defaults={"agreed": agreed, "agreed_at": now},
        )
    return list(UserTermAgreement.objects.filter(user_id=user_id, term_id__in=decisions).order_by("term_id"))
