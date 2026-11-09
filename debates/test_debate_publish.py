from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from accounts.models import User
from debates.models import Category, Debate, DebateImage
from economy.models import CoinTransaction


class DebatePublishTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user("writer@example.com", "Writer", coin_balance=3)
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
