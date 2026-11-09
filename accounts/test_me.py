from base64 import b64encode
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User, UserIdentityVerification, UserInterestCategory
from debates.models import Category
from economy.models import Badge, UserBadge


PROFILE_FIELDS = {"id", "status", "identityVerified", "nickname", "interests", "credit", "badges"}


class MeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("me")
        self.user = User.objects.create_user("private@example.com", "Member", password="secret-pass")
        self.client.force_authenticate(self.user)

    def me(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def verification(self, status):
        return UserIdentityVerification.objects.create(
            user=self.user, provider="PASS", request_id="req-1", status=status,
            identity_key_hash="f" * 64, expires_at=timezone.now() + timedelta(minutes=5),
        )

    def test_response_shape(self):
        body = self.me()

        self.assertEqual(set(body), {"data"})
        data = body["data"]
        self.assertEqual(set(data), PROFILE_FIELDS)
        self.assertEqual(data["id"], str(self.user.pk))
        self.assertEqual(data["status"], "ACTIVE")
        self.assertEqual(data["nickname"], "Member")
        self.assertEqual(data["credit"], "0.000000")
        self.assertIs(data["identityVerified"], False)
        self.assertEqual(data["interests"], [])
        self.assertEqual(data["badges"], [])

    def test_credit_has_six_decimals(self):
        User.objects.filter(pk=self.user.pk).update(credit=Decimal("12.5"))
        self.client.force_authenticate(User.objects.get(pk=self.user.pk))

        self.assertEqual(self.me()["data"]["credit"], "12.500000")

    def test_identity_verified(self):
        verification = self.verification(UserIdentityVerification.Status.PENDING)
        self.assertIs(self.me()["data"]["identityVerified"], False)

        verification.status = UserIdentityVerification.Status.VERIFIED
        verification.save()
        self.assertIs(self.me()["data"]["identityVerified"], True)

    def test_interests_are_active_and_sorted(self):
        late = Category.objects.create(name="Late", sort_order=2)
        early = Category.objects.create(name="Early", sort_order=1)
        hidden = Category.objects.create(name="Hidden", sort_order=0, is_active=False)
        for category in (late, early, hidden):
            UserInterestCategory.objects.create(user=self.user, category=category)

        self.assertEqual(self.me()["data"]["interests"], [
            {"id": str(early.pk), "name": "Early", "sortOrder": 1},
            {"id": str(late.pk), "name": "Late", "sortOrder": 2},
        ])

    def test_badges_are_ordered_by_acquired_at(self):
        now = timezone.now()
        newer = Badge.objects.create(name="Newer", icon_url="https://example.com/n.png", required_credit=10)
        older = Badge.objects.create(name="Older", icon_url="https://example.com/o.png", required_credit=1)
        UserBadge.objects.create(user=self.user, badge=newer, is_representative=True)
        UserBadge.objects.create(user=self.user, badge=older)
        UserBadge.objects.filter(badge=newer).update(acquired_at=now)
        UserBadge.objects.filter(badge=older).update(acquired_at=now - timedelta(days=1))

        badges = self.me()["data"]["badges"]

        self.assertEqual([b["id"] for b in badges], [str(older.pk), str(newer.pk)])
        for badge in badges:
            self.assertEqual(set(badge), {"id", "name", "iconUrl", "acquiredAt", "isRepresentative"})
            self.assertTrue(badge["acquiredAt"].endswith("Z"))
        self.assertEqual(badges[1]["name"], "Newer")
        self.assertEqual(badges[1]["iconUrl"], "https://example.com/n.png")
        self.assertIs(badges[1]["isRepresentative"], True)
        self.assertIs(badges[0]["isRepresentative"], False)

    def test_private_values_are_not_exposed(self):
        self.verification(UserIdentityVerification.Status.VERIFIED)

        text = self.client.get(self.url).content.decode()

        for value in ("private@example.com", self.user.password, "f" * 64, "email", "password"):
            self.assertNotIn(value, text)

    def test_suspended_member_can_read(self):
        self.user.status = User.Status.SUSPENDED
        self.user.save()

        self.assertEqual(self.me()["data"]["status"], "SUSPENDED")

    def test_requires_login(self):
        self.client.force_authenticate(None)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTH_REQUIRED")

    def test_invalid_credentials_use_profile_auth_error(self):
        self.client.force_authenticate(None)
        credentials = b64encode(f"{self.user.pk}:wrong-password".encode()).decode()

        response = self.client.get(self.url, HTTP_AUTHORIZATION=f"Basic {credentials}")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTH_REQUIRED")
        self.assertNotIn("wrong-password", response.content.decode())

    def test_other_endpoints_keep_their_auth_error(self):
        self.client.force_authenticate(None)

        response = self.client.get("/v1/me/debates")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_REQUIRED")
