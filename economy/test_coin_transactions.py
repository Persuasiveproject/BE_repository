from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from debates.models import Category, Debate
from economy.models import CoinTransaction


class CoinTransactionListTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user("member@example.com", "Member")
        self.other = User.objects.create_user("other@example.com", "Other")
        self.client = APIClient()
        self.client.force_authenticate(self.member)
        self.url = reverse("coin-transactions")

    def entry(self, **overrides):
        fields = {"user": self.member, "type": CoinTransaction.Type.REWARD, "amount": 1, "balance_after": 3}
        return CoinTransaction.objects.create(**(fields | overrides))

    def test_contract_only_contains_own_transactions_and_uses_string_refs(self):
        debate = Debate.objects.create(
            creator=self.member, category=Category.objects.create(name="Category"), type=Debate.Type.ENDLESS,
            title="Canceled", content="Topic", status=Debate.Status.CANCELED,
        )
        charge = self.entry(
            type=CoinTransaction.Type.DEBATE_CREATE, amount=-1, balance_after=2,
            ref_type=CoinTransaction.RefType.DEBATE, ref_id=debate.pk,
        )
        refund = self.entry(
            type=CoinTransaction.Type.REFUND, ref_type=CoinTransaction.RefType.DEBATE, ref_id=debate.pk,
            reverses_transaction=charge,
        )
        self.entry(user=self.other)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"data", "meta"})
        self.assertEqual(set(body["data"]), {"transactions"})
        self.assertEqual(body["meta"], {"nextCursor": None})
        first, second = body["data"]["transactions"]
        self.assertEqual(first, {
            "id": str(refund.pk), "type": "REFUND", "amount": 1, "balanceAfter": 3,
            "refType": "DEBATE", "refId": str(debate.pk), "reversesTransactionId": str(charge.pk),
            "createdAt": refund.created_at.isoformat().replace("+00:00", "Z"),
        })
        self.assertEqual(second["id"], str(charge.pk))
        self.assertIsNone(second["reversesTransactionId"])

    def test_cursor_preserves_order_for_equal_timestamps_and_is_user_scoped(self):
        ids = [self.entry().pk for _ in range(21)]
        CoinTransaction.objects.filter(pk__in=ids).update(created_at=timezone.now())
        body = self.client.get(self.url).json()
        self.assertEqual([row["id"] for row in body["data"]["transactions"]], [str(pk) for pk in ids[:0:-1]])
        cursor = body["meta"]["nextCursor"]
        body = self.client.get(self.url, {"cursor": cursor}).json()
        self.assertEqual([row["id"] for row in body["data"]["transactions"]], [str(ids[0])])
        self.assertEqual(body["meta"], {"nextCursor": None})
        self.client.force_authenticate(self.other)
        response = self.client.get(self.url, {"cursor": cursor})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "CURSOR_INVALID")

    def test_hidden_and_unresolvable_references_are_null_and_login_is_required(self):
        other_entry = self.entry(user=self.other)
        self.entry(ref_type=CoinTransaction.RefType.PAYMENT, ref_id=999, reverses_transaction=other_entry)
        row = self.client.get(self.url).json()["data"]["transactions"][0]
        self.assertIsNone(row["refId"])
        self.assertIsNone(row["reversesTransactionId"])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)
