from collections import defaultdict
from datetime import datetime

from django.core import signing
from django.db.models import Q
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import MAX_ID, ContractError, ContractResponseMixin
from debates.models import Debate
from debates.serializers import utc
from discussions.models import Persuasion

from .models import AdRewardLog, CoinTransaction, Payment


COIN_TRANSACTION_PAGE_SIZE = 20


def accessible_coin_references(entries, user):
    ids = defaultdict(set)
    for entry in entries:
        if entry.ref_id is not None:
            ids[entry.ref_type].add(entry.ref_id)

    ref = CoinTransaction.RefType
    targets = {
        ref.DEBATE: Debate.objects.exclude(status=Debate.Status.DRAFT),
        ref.PAYMENT: Payment.objects.filter(user=user),
        ref.AD_REWARD: AdRewardLog.objects.filter(user=user),
        ref.PERSUASION: Persuasion.objects.filter(opinion__participant__user=user),
    }
    return {
        ref.USER: ids[ref.USER] & {user.pk},
        **{
            kind: set(queryset.filter(pk__in=ids[kind]).values_list("pk", flat=True))
            for kind, queryset in targets.items() if ids[kind]
        },
    }


def coin_transaction_data(entry, references):
    ref_id = entry.ref_id if entry.ref_id in references.get(entry.ref_type, ()) else None
    original = entry.reverses_transaction
    original_id = original.pk if original is not None and original.user_id == entry.user_id else None
    return {
        "id": str(entry.pk),
        "amount": entry.amount,
        "type": entry.type,
        "balanceAfter": entry.balance_after,
        "refType": entry.ref_type,
        "refId": str(ref_id) if ref_id is not None else None,
        "reversesTransactionId": str(original_id) if original_id is not None else None,
        "createdAt": utc(entry.created_at),
    }


class CoinTransactionListView(ContractResponseMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        transactions = CoinTransaction.objects.filter(user=request.user).select_related("reverses_transaction")
        salt = f"economy.coin-transactions:{request.user.pk}"
        cursor = request.query_params.get("cursor")
        if cursor is not None:
            try:
                created_at, row_id = signing.loads(cursor, salt=salt)
                timestamp = datetime.fromisoformat(created_at)
                if not timezone.is_aware(timestamp) or type(row_id) is not int or not 1 <= row_id <= MAX_ID:
                    raise ValueError("Invalid cursor position")
            except (signing.BadSignature, TypeError, ValueError) as exc:
                raise ContractError(400, "CURSOR_INVALID", "Use a valid cursor.") from exc
            transactions = transactions.filter(
                Q(created_at__lt=timestamp) | Q(created_at=timestamp, pk__lt=row_id),
            )

        page = list(transactions.order_by("-created_at", "-id")[:COIN_TRANSACTION_PAGE_SIZE + 1])
        next_cursor = None
        if len(page) > COIN_TRANSACTION_PAGE_SIZE:
            page.pop()
            last = page[-1]
            next_cursor = signing.dumps([utc(last.created_at), last.pk], salt=salt)
        references = accessible_coin_references(page, request.user)
        return Response({
            "data": {"transactions": [coin_transaction_data(entry, references) for entry in page]},
            "meta": {"nextCursor": next_cursor},
        })



class WalletViews(ContractResponseMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            "data":{
                "coinBalance" : request.user.coin_balance,
            }
        })