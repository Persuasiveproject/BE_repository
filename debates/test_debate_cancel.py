from base64 import b64encode
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from debates.models import Category, Debate
from economy.models import CoinTransaction


class DebateCancelTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.operator = User.objects.create_user("operator@example.com", "Operator", role=User.Role.ADMIN)
        self.creator = User.objects.create_user("author@example.com", "Author", coin_balance=2)
        now = timezone.now()
        self.debate = Debate.objects.create(
            creator=self.creator, category=Category.objects.create(name="Technology"),
            type=Debate.Type.QUICK, duration_minutes=10, title="Title", content="Content",
            status=Debate.Status.OPEN, coin_cost=1, started_at=now, ends_at=now + timedelta(minutes=10),
        )
        self.charge = CoinTransaction.objects.create(
            user=self.creator, amount=-1, type=CoinTransaction.Type.DEBATE_CREATE, balance_after=2,
            ref_type=CoinTransaction.RefType.DEBATE, ref_id=self.debate.pk,
        )
        self.url = reverse("debate-cancel", args=[self.debate.pk])
        self.client.force_authenticate(self.operator)

    def cancel(self, **body):
        return self.client.post(self.url, body, format="json")

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["error"]["code"], code)

    def test_cancel_refunds_and_links_original_charge_exactly_once(self):
        response = self.cancel(refund=True, cancelReason="Operational issue")
        self.assertEqual(response.status_code, 200)
        reversal = CoinTransaction.objects.get(type=CoinTransaction.Type.REFUND)
        self.assertEqual(response.json(), {"data": {
            "debateId": str(self.debate.pk), "status": "CANCELED",
            "refundedCoin": 1, "refundTransactionId": str(reversal.pk),
        }})
        self.debate.refresh_from_db()
        self.creator.refresh_from_db()
        self.assertEqual(self.debate.status, Debate.Status.CANCELED)
        self.assertEqual(self.debate.canceled_by_id, self.operator.pk)
        self.assertIsNotNone(self.debate.canceled_at)
        self.assertEqual(self.debate.cancel_reason, "Operational issue")
        self.assertEqual(self.creator.coin_balance, 3)
        self.assertEqual(
            (reversal.amount, reversal.balance_after, reversal.user_id, reversal.ref_type,
             reversal.ref_id, reversal.reverses_transaction_id, reversal.created_by_id),
            (1, 3, self.creator.pk, "DEBATE", self.debate.pk, self.charge.pk, self.operator.pk),
        )
        self.assert_error(self.cancel(refund=True), 409, "REFUND_ALREADY_PROCESSED")
        self.creator.refresh_from_db()
        self.assertEqual(self.creator.coin_balance, 3)
        self.assertEqual(CoinTransaction.objects.count(), 2)

    def test_cancel_without_refund(self):
        response = self.cancel(refund=False, cancelReason=None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"data": {
            "debateId": str(self.debate.pk), "status": "CANCELED",
            "refundedCoin": 0, "refundTransactionId": None,
        }})
        self.assert_error(self.cancel(refund=True), 409, "INVALID_DEBATE_STATE")
        self.creator.refresh_from_db()
        self.assertEqual(self.creator.coin_balance, 2)
        self.assertEqual(CoinTransaction.objects.count(), 1)

    def test_authentication_and_active_admin_role_are_required(self):
        self.client.force_authenticate(None)
        self.assert_error(self.cancel(refund=True), 401, "AUTHENTICATION_REQUIRED")
        self.client.force_authenticate(self.creator)
        self.assert_error(self.cancel(refund=True), 403, "ADMIN_REQUIRED")
        self.operator.status = User.Status.SUSPENDED
        self.operator.save(update_fields=["status"])
        self.client.force_authenticate(self.operator)
        self.assert_error(self.cancel(refund=True), 403, "ADMIN_REQUIRED")

    def test_invalid_body_and_missing_debate(self):
        for body in ({}, {"refund": None}, {"refund": "true"}, {"refund": 1}, {"refund": True, "other": 1},
                     {"refund": True, "cancelReason": 1}, {"refund": True, "cancelReason": "a" * 256}):
            with self.subTest(body=body):
                self.assert_error(self.cancel(**body), 400, "VALIDATION_ERROR")
        url = reverse("debate-cancel", args=[self.debate.pk + 1])
        self.assert_error(self.client.post(url, {"refund": True}, format="json"), 404, "NOT_FOUND")

    def test_draft_closed_and_overdue_debates_cannot_be_canceled(self):
        for status in (Debate.Status.DRAFT, Debate.Status.CLOSED, Debate.Status.OPEN):
            with self.subTest(status=status):
                Debate.objects.filter(pk=self.debate.pk).update(status=status, ends_at=timezone.now())
                self.assert_error(self.cancel(refund=True), 409, "INVALID_DEBATE_STATE")
        self.assertEqual(CoinTransaction.objects.count(), 1)

    def test_missing_or_inconsistent_original_charge_leaves_debate_open(self):
        for amount in (-2, -1):
            with self.subTest(amount=amount):
                self.charge.amount = amount
                self.charge.ref_id = self.debate.pk if amount == -2 else self.debate.pk + 1
                self.charge.save(update_fields=["amount", "ref_id"])
                self.assert_error(self.cancel(refund=True), 409, "INVALID_DEBATE_STATE")
        self.debate.refresh_from_db()
        self.creator.refresh_from_db()
        self.assertEqual(self.debate.status, Debate.Status.OPEN)
        self.assertEqual(self.creator.coin_balance, 2)
        self.assertEqual(CoinTransaction.objects.count(), 1)

    def test_ledger_failure_rolls_back_refund(self):
        with patch("debates.cancel.CoinTransaction.objects.create", side_effect=RuntimeError("ledger failure")):
            with self.assertRaisesMessage(RuntimeError, "ledger failure"):
                self.cancel(refund=True)
        self.debate.refresh_from_db()
        self.creator.refresh_from_db()
        self.assertEqual(self.debate.status, Debate.Status.OPEN)
        self.assertEqual(self.creator.coin_balance, 2)
        self.assertEqual(CoinTransaction.objects.count(), 1)

    def test_published_debate_refund_through_authenticated_http_endpoints(self):
        draft = Debate.objects.create(
            creator=self.creator, category=self.debate.category,
            type=Debate.Type.QUICK, duration_minutes=10, title="New debate", content="Content",
        )
        self.client.force_authenticate(self.creator)
        publish = self.client.post(
            reverse("debate-publish", args=[draft.pk]),
            HTTP_IDEMPOTENCY_KEY="publish-then-cancel",
        )
        self.assertEqual(publish.status_code, 200)
        self.assertEqual(publish.json()["data"]["coinBalance"], 1)

        self.operator.set_password("local-test-password")
        self.operator.save(update_fields=["password"])
        self.client.force_authenticate(None)
        credentials = b64encode(f"{self.operator.pk}:local-test-password".encode()).decode()
        url = reverse("debate-cancel", args=[draft.pk])
        response = self.client.post(url, {"refund": True}, format="json", HTTP_AUTHORIZATION=f"Basic {credentials}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["refundedCoin"], 1)
        self.creator.refresh_from_db()
        self.assertEqual(self.creator.coin_balance, 2)
