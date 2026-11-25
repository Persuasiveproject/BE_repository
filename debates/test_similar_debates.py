from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User, UserIdentityVerification
from debates.models import Category, Debate


class SimilarDebateTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.user = User.objects.create_user("member@example.com", "Member")
        self.category = Category.objects.create(name="Category")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = reverse("debate-similar")

    def verify_identity(self):
        UserIdentityVerification.objects.create(
            user=self.user, request_id="local", provider="test", expires_at=self.now + timedelta(minutes=2),
            status=UserIdentityVerification.Status.VERIFIED,
        )

    def debate(self, title, status=Debate.Status.OPEN):
        return Debate.objects.create(
            creator=self.user, category=self.category, title=title, content="topic",
            type=Debate.Type.ENDLESS, status=status, participant_count=7,
            started_at=self.now, ends_at=self.now + timedelta(hours=72),
        )

    def test_ranking_visibility_and_limit(self):
        self.verify_identity()
        exact = self.debate("Rail budget")
        both = self.debate("Annual rail budget", Debate.Status.CLOSED)
        partial = [self.debate(f"Rail project {i}") for i in range(6)]
        self.debate("Rail budget", Debate.Status.DRAFT)
        self.debate("Rail budget", Debate.Status.CANCELED)
        self.debate("No match")
        response = self.client.get(self.url, {"title": "Rail budget"})
        self.assertEqual(response.status_code, 200)
        rows = response.json()["data"]["debates"]
        ids = [exact.pk, both.pk, *[d.pk for d in reversed(partial[-3:])]]
        self.assertEqual([item["id"] for item in rows], [str(pk) for pk in ids])
        self.assertEqual(rows[0]["participantCount"], 7)
        self.assertEqual(rows[1]["status"], "CLOSED")

    def test_authentication_and_query_validation(self):
        self.assertEqual(self.client.get(self.url, {"title": "Rail"}).status_code, 403)
        self.verify_identity()
        for query in ("", "?", "x" * 101):
            with self.subTest(query=query):
                response = self.client.get(self.url, {"title": query})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "TITLE_INVALID")
        self.assertEqual(self.client.get(self.url, {"title": "Rail"}).json(), {"data": {"debates": []}})
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url, {"title": "Rail"}).status_code, 401)
