from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from economy.models import Badge, CreditHistory, UserBadge


class CreditAndBadgeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.credits_url = reverse("me-credits")
        self.badges_url = reverse("me-badges")
        self.history_url = reverse("me-credit-history")
        self.user = User.objects.create_user(
            "credits@example.com",
            "Member",
            credit=Decimal("30.125"),
        )
        self.other = User.objects.create_user(
            "other-credits@example.com",
            "Other",
            credit=Decimal("99"),
        )
        self.client.force_authenticate(self.user)

    def test_credits_response_shape_and_badge_order(self):
        now = timezone.now()
        newer = Badge.objects.create(name="Newer", icon_url="https://example.com/n.png", required_credit=10)
        older = Badge.objects.create(name="Older", icon_url="https://example.com/o.png", required_credit=1)
        other_badge = Badge.objects.create(name="Other", icon_url="https://example.com/x.png", required_credit=20)
        UserBadge.objects.create(user=self.user, badge=newer, is_representative=True)
        UserBadge.objects.create(user=self.user, badge=older)
        UserBadge.objects.create(user=self.other, badge=other_badge)
        UserBadge.objects.filter(user=self.user, badge=newer).update(acquired_at=now)
        UserBadge.objects.filter(user=self.user, badge=older).update(acquired_at=now - timedelta(days=1))

        response = self.client.get(self.credits_url)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"data"})
        self.assertEqual(set(body["data"]), {"credit", "badges"})
        self.assertEqual(body["data"]["credit"], "30.125000")
        badges = body["data"]["badges"]
        self.assertEqual([b["id"] for b in badges], [str(older.pk), str(newer.pk)])
        for badge in badges:
            self.assertEqual(set(badge), {"id", "name", "iconUrl", "acquiredAt", "isRepresentative"})
            self.assertTrue(badge["acquiredAt"].endswith("Z"))
        self.assertIs(badges[0]["isRepresentative"], False)
        self.assertIs(badges[1]["isRepresentative"], True)

    def test_badges_response_shape(self):
        badge = Badge.objects.create(name="Starter", icon_url="https://example.com/s.png", required_credit=0)
        UserBadge.objects.create(user=self.user, badge=badge, is_representative=True)

        response = self.client.get(self.badges_url)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"data"})
        self.assertEqual(set(body["data"]), {"badges"})
        self.assertEqual(len(body["data"]["badges"]), 1)
        item = body["data"]["badges"][0]
        self.assertEqual(set(item), {"id", "name", "iconUrl", "acquiredAt", "isRepresentative"})
        self.assertEqual(item["id"], str(badge.pk))
        self.assertEqual(item["name"], "Starter")
        self.assertEqual(item["iconUrl"], "https://example.com/s.png")
        self.assertTrue(item["acquiredAt"].endswith("Z"))
        self.assertIs(item["isRepresentative"], True)

    def test_credit_history_response_shape_and_isolation(self):
        CreditHistory.objects.create(
            user=self.other, amount=Decimal("5.000000"), reason="SIGNUP_BONUS", ref_id=None,
        )
        first = CreditHistory.objects.create(
            user=self.user, amount=Decimal("-1.000000"), reason="REPORT_PENALTY", ref_id=None,
        )
        second = CreditHistory.objects.create(
            user=self.user, amount=Decimal("0.125000"), reason="PERSUASION_REWARD", ref_id=77,
        )

        response = self.client.get(self.history_url)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"data", "meta"})
        self.assertEqual(set(body["data"]), {"histories"})
        self.assertEqual(body["meta"], {"nextCursor": None})

        histories = body["data"]["histories"]
        self.assertEqual([h["id"] for h in histories], [str(second.pk), str(first.pk)])
        for item in histories:
            self.assertEqual(set(item), {"id", "amount", "reason", "refId", "createdAt"})
            self.assertTrue(item["createdAt"].endswith("Z"))

        self.assertEqual(histories[0]["amount"], "0.125000")
        self.assertEqual(histories[0]["reason"], "PERSUASION_REWARD")
        self.assertEqual(histories[0]["refId"], "77")

        self.assertEqual(histories[1]["amount"], "-1.000000")
        self.assertEqual(histories[1]["reason"], "REPORT_PENALTY")
        self.assertIsNone(histories[1]["refId"])

    def test_credit_history_cursor_pagination_of_20(self):
        records = [
            CreditHistory.objects.create(
                user=self.user,
                amount=Decimal("0.100000"),
                reason="PERSUASION_REWARD",
                ref_id=index + 1,
            )
            for index in range(21)
        ]

        first = self.client.get(self.history_url)
        self.assertEqual(first.status_code, 200)
        first_body = first.json()
        self.assertEqual(len(first_body["data"]["histories"]), 20)
        self.assertIsNotNone(first_body["meta"]["nextCursor"])
        self.assertEqual(
            [h["id"] for h in first_body["data"]["histories"]],
            [str(r.pk) for r in reversed(records[1:])],
        )

        second = self.client.get(self.history_url, {"cursor": first_body["meta"]["nextCursor"]})
        self.assertEqual(second.status_code, 200)
        second_body = second.json()
        self.assertEqual([h["id"] for h in second_body["data"]["histories"]], [str(records[0].pk)])
        self.assertIsNone(second_body["meta"]["nextCursor"])

    def test_credit_history_invalid_cursor(self):
        response = self.client.get(self.history_url, {"cursor": "not-a-cursor"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "CURSOR_INVALID")

    def test_endpoints_require_active_member(self):
        urls = (self.credits_url, self.badges_url, self.history_url)

        self.client.force_authenticate(None)
        for url in urls:
            with self.subTest(phase="anon", url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_REQUIRED")

        self.user.status = User.Status.SUSPENDED
        self.user.save(update_fields=["status"])
        self.client.force_authenticate(self.user)
        for url in urls:
            with self.subTest(phase="suspended", url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error"]["code"], "FORBIDDEN")
