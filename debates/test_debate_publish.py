from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as utc_timezone
from threading import Barrier
from unittest.mock import patch

from django.db import connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from accounts.models import APIIdempotencyRecord, User, UserIdentityVerification
from debates.models import Category, Debate, DebateImage
from economy.models import CoinTransaction


def verify_identity(user):
    now = timezone.now()
    return UserIdentityVerification.objects.create(
        user=user, provider="test", request_id="publish-fixture", status="VERIFIED",
        identity_key_hash=f"{user.pk:064x}", verified_at=now,
        expires_at=now + timedelta(minutes=5),
    )


class DebatePublishTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user("writer@example.com", "Writer", coin_balance=3)
        self.identity = verify_identity(self.user)
        self.category = Category.objects.create(name="Politics")
        self.debate = self.draft()
        self.client.force_authenticate(self.user)

    def draft(self, **fields):
        values = {
            "creator": self.user, "category": self.category, "type": "QUICK",
            "title": "Title", "content": "Content", "duration_minutes": 10,
        }
        values.update(fields)
        return Debate.objects.create(**values)

    def publish(self, debate=None, key="key-1"):
        headers = {} if key is None else {"HTTP_IDEMPOTENCY_KEY": key}
        url = reverse("debate-publish", args=[(debate or self.debate).pk])
        return self.client.post(url, **headers)

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["error"]["code"], code)

    def balance(self):
        self.user.refresh_from_db()
        return self.user.coin_balance

    def test_publishes_quick_debate(self):
        response = self.publish()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"data"})
        data = body["data"]
        self.assertEqual(set(data), {"debateId", "status", "startedAt", "endsAt", "coinBalance"})
        self.assertEqual(data["debateId"], str(self.debate.pk))
        self.assertEqual(data["status"], "OPEN")
        self.assertIs(type(data["coinBalance"]), int)
        self.assertEqual(data["coinBalance"], 2)
        self.assertTrue(data["startedAt"].endswith("Z"))
        self.assertTrue(data["endsAt"].endswith("Z"))

        self.debate.refresh_from_db()
        self.assertEqual(self.debate.status, Debate.Status.OPEN)
        self.assertEqual(self.debate.coin_cost, 1)
        self.assertEqual(self.debate.ends_at, self.debate.started_at + timedelta(minutes=10))
        self.assertEqual(parse_datetime(data["startedAt"]), self.debate.started_at)
        self.assertEqual(parse_datetime(data["endsAt"]), self.debate.ends_at)
        self.assertEqual(self.balance(), 2)
        entry = CoinTransaction.objects.get()
        self.assertEqual(
            (entry.user_id, entry.amount, entry.type, entry.balance_after, entry.ref_type, entry.ref_id),
            (self.user.pk, -1, "DEBATE_CREATE", 2, "DEBATE", self.debate.pk),
        )

    def test_endless_ends_after_72_hours(self):
        debate = self.draft(type="ENDLESS", duration_minutes=None)

        self.assertEqual(self.publish(debate).status_code, 200)

        debate.refresh_from_db()
        self.assertEqual(debate.ends_at, debate.started_at + timedelta(hours=72))

    def test_same_key_replays_first_response(self):
        first = self.publish()
        second = self.publish()

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(self.balance(), 2)
        self.assertEqual(CoinTransaction.objects.count(), 1)

    def test_unverified_creator_cannot_publish_or_spend_coins(self):
        UserIdentityVerification.objects.filter(user=self.user).delete()
        for status in (None, "PENDING", "FAILED"):
            with self.subTest(status=status):
                if status is not None:
                    UserIdentityVerification.objects.update_or_create(
                        user=self.user, defaults={
                            "provider": "test", "request_id": "publish-fixture", "status": status,
                            "expires_at": timezone.now() + timedelta(minutes=5),
                        },
                    )
                response = self.publish()
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json(), {"error": {
                    "code": "IDENTITY_VERIFICATION_REQUIRED", "message": "Verify identity to continue.",
                }})
                self.debate.refresh_from_db()
                self.assertEqual(self.debate.status, Debate.Status.DRAFT)
                self.assertEqual(self.debate.coin_cost, 0)
                self.assertIsNone(self.debate.started_at)
                self.assertIsNone(self.debate.ends_at)
                self.assertEqual(self.balance(), 3)
                self.assertFalse(CoinTransaction.objects.exists())
                self.assertFalse(APIIdempotencyRecord.objects.exists())

        UserIdentityVerification.objects.filter(user=self.user).delete()
        verify_identity(self.user)
        self.assertEqual(self.publish().status_code, 200)
        self.assertEqual(self.balance(), 2)

    def test_replay_requires_current_verified_identity(self):
        self.assertEqual(self.publish().status_code, 200)
        UserIdentityVerification.objects.filter(pk=self.identity.pk).update(status="FAILED")

        self.assert_error(self.publish(), 403, "IDENTITY_VERIFICATION_REQUIRED")
        self.assertEqual(self.balance(), 2)
        self.assertEqual(CoinTransaction.objects.count(), 1)
        self.assertEqual(APIIdempotencyRecord.objects.count(), 1)

    def test_other_key_after_publish_conflicts(self):
        self.publish()

        self.assert_error(self.publish(key="key-2"), 409, "NOT_DRAFT")
        self.assertEqual(self.balance(), 2)

    def test_same_key_on_other_debate_is_separate(self):
        other = self.draft()

        self.assertEqual(self.publish().status_code, 200)
        response = self.publish(other)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["debateId"], str(other.pk))
        self.assertEqual(self.balance(), 1)
        self.assertEqual(CoinTransaction.objects.count(), 2)

    def test_invalid_key(self):
        for key in (None, "", "   ", "a" * 129):
            self.assert_error(self.publish(key=key), 400, "IDEMPOTENCY_KEY_INVALID")
        self.debate.refresh_from_db()
        self.assertEqual(self.debate.status, Debate.Status.DRAFT)

    def test_insufficient_coins(self):
        User.objects.filter(pk=self.user.pk).update(coin_balance=0)

        self.assert_error(self.publish(), 409, "INSUFFICIENT_COINS")
        self.debate.refresh_from_db()
        self.assertEqual(self.debate.status, Debate.Status.DRAFT)
        self.assertFalse(CoinTransaction.objects.exists())

    def test_images_must_be_ready(self):
        DebateImage.objects.create(debate=self.debate, status=DebateImage.Status.READY)
        image = DebateImage.objects.create(debate=self.debate, status=DebateImage.Status.CHECKING)

        self.assert_error(self.publish(), 409, "ASSET_NOT_READY")
        self.assertEqual(self.balance(), 3)

        image.status = DebateImage.Status.READY
        image.save()
        self.assertEqual(self.publish().status_code, 200)

    def test_other_member_forbidden(self):
        self.client.force_authenticate(User.objects.create_user("other@example.com", "Other", coin_balance=3))

        self.assert_error(self.publish(), 403, "NOT_DEBATE_CREATOR")
        self.debate.refresh_from_db()
        self.assertEqual(self.debate.status, Debate.Status.DRAFT)

    def test_already_open(self):
        Debate.objects.filter(pk=self.debate.pk).update(status=Debate.Status.OPEN)

        self.assert_error(self.publish(), 409, "NOT_DRAFT")
        self.assertEqual(self.balance(), 3)

    def test_missing_debate(self):
        response = self.client.post(
            reverse("debate-publish", args=[999999]), HTTP_IDEMPOTENCY_KEY="key-1",
        )

        self.assert_error(response, 404, "NOT_FOUND")

    def test_requires_login(self):
        self.client.force_authenticate(None)

        self.assert_error(self.publish(), 401, "AUTHENTICATION_REQUIRED")

    def test_retry_with_same_key_after_failure(self):
        User.objects.filter(pk=self.user.pk).update(coin_balance=0)
        self.assert_error(self.publish(), 409, "INSUFFICIENT_COINS")

        User.objects.filter(pk=self.user.pk).update(coin_balance=1)
        response = self.publish()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["coinBalance"], 0)

    def test_active_limit_ignores_expired_debates_and_keeps_replay(self):
        now = datetime(2026, 11, 18, 1, tzinfo=utc_timezone.utc)
        previous_day = now - timedelta(hours=11)
        for _ in range(2):
            self.draft(status="OPEN", started_at=previous_day, ends_at=now + timedelta(hours=1))
        self.draft(status="OPEN", started_at=previous_day, ends_at=now)
        other = self.draft()

        with patch("debates.publish.timezone.now", return_value=now):
            first = self.publish()
            self.assertEqual(first.status_code, 200)
            self.assertEqual(self.publish().json(), first.json())
            denied = self.publish(other)

        self.assert_error(denied, 409, "PUBLISH_LIMIT_EXCEEDED")
        self.assertEqual(denied.json()["error"]["details"], {"limitType": "ACTIVE", "limit": 3})
        other.refresh_from_db()
        self.assertEqual(other.status, Debate.Status.DRAFT)
        self.assertEqual(self.balance(), 2)
        self.assertEqual(CoinTransaction.objects.count(), 1)

    def test_daily_limit_counts_previous_publications_and_resets_at_kst_midnight(self):
        midnight = datetime(2026, 11, 18, 15, tzinfo=utc_timezone.utc)
        for status in ("CLOSED", "CANCELED", "OPEN"):
            self.draft(status=status, started_at=midnight - timedelta(hours=2), ends_at=midnight - timedelta(hours=1))

        with patch("debates.publish.timezone.now", return_value=midnight - timedelta(microseconds=1)):
            denied = self.publish()
        self.assert_error(denied, 409, "PUBLISH_LIMIT_EXCEEDED")
        self.assertEqual(denied.json()["error"]["details"], {"limitType": "DAILY", "limit": 3})
        self.assertEqual(self.balance(), 3)
        self.assertFalse(CoinTransaction.objects.exists())

        with patch("debates.publish.timezone.now", return_value=midnight):
            self.assertEqual(self.publish().status_code, 200)


class DebatePublishLimitConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_two_drafts_cannot_take_the_same_last_daily_slot(self):
        now = datetime(2026, 11, 18, 1, tzinfo=utc_timezone.utc)
        user = User.objects.create_user("writer@example.com", "Writer", coin_balance=5)
        verify_identity(user)
        category = Category.objects.create(name="Politics")
        fields = dict(creator=user, category=category, type="QUICK", title="Title", content="Body", duration_minutes=10)
        for _ in range(2):
            Debate.objects.create(**fields, status="CLOSED", started_at=now - timedelta(hours=1), closed_at=now)
        drafts = [Debate.objects.create(**fields) for _ in range(2)]
        ready = Barrier(2)

        def submit(debate):
            try:
                client = APIClient()
                client.force_authenticate(user)
                ready.wait(timeout=10)
                response = client.post(reverse("debate-publish", args=[debate.pk]), HTTP_IDEMPOTENCY_KEY="publish")
                return response.status_code, response.json()
            finally:
                connections.close_all()

        with patch("debates.publish.timezone.now", return_value=now), ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, drafts))

        self.assertCountEqual([status for status, _ in responses], [200, 409])
        error = next(body["error"] for status, body in responses if status == 409)
        self.assertEqual(error["code"], "PUBLISH_LIMIT_EXCEEDED")
        self.assertEqual(error["details"], {"limitType": "DAILY", "limit": 3})
        user.refresh_from_db()
        self.assertEqual(user.coin_balance, 4)
        self.assertEqual(CoinTransaction.objects.filter(user=user).count(), 1)
        self.assertEqual(Debate.objects.filter(pk__in=[draft.pk for draft in drafts], status="DRAFT").count(), 1)
