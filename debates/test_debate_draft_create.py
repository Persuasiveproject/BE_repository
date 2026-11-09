from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from debates.models import Category, Debate


class DebateDraftCreateTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("debate-draft-create")
        self.user = User.objects.create_user("writer@example.com", "Writer", coin_balance=50)
        self.category = Category.objects.create(name="Politics")
        self.client.force_authenticate(self.user)

    def body(self, **overrides):
        body = {
            "categoryId": str(self.category.pk), "kind": "QUICK",
            "title": "Title", "content": "Content", "durationMinutes": 10,
        }
        body.update(overrides)
        return body

    def post(self, body):
        return self.client.post(self.url, body, format="json")

    def assert_invalid(self, body, field):
        response = self.post(body)
        self.assertEqual(response.status_code, 400, body)
        self.assertEqual(response.json()["error"]["code"], "DRAFT_INVALID")
        self.assertIn(field, response.json()["error"]["details"])
        self.assertFalse(Debate.objects.exists())

    def test_creates_quick_draft(self):
        response = self.post(self.body(title=" Title ", ignored="x"))

        self.assertEqual(response.status_code, 201)
        debate = Debate.objects.get()
        self.assertEqual(debate.status, Debate.Status.DRAFT)
        self.assertEqual(debate.creator, self.user)
        self.assertEqual(debate.category, self.category)
        self.assertEqual(debate.type, "QUICK")
        self.assertEqual(debate.title, " Title ")
        self.assertEqual(debate.content, "Content")
        self.assertEqual(debate.duration_minutes, 10)
        self.assertIsNone(debate.started_at)
        self.assertIsNone(debate.ends_at)
        self.user.refresh_from_db()
        self.assertEqual(self.user.coin_balance, 50)

    def test_creates_endless_draft_without_duration(self):
        omitted = self.body(kind="ENDLESS")
        del omitted["durationMinutes"]
        for body in (omitted, self.body(kind="ENDLESS", durationMinutes=None)):
            response = self.post(body)
            self.assertEqual(response.status_code, 201)
            self.assertIsNone(Debate.objects.get(pk=response.json()["data"]["debateId"]).duration_minutes)

    def test_response_shape(self):
        body = self.post(self.body()).json()

        self.assertEqual(set(body), {"data"})
        self.assertEqual(set(body["data"]), {"debateId", "status", "createdAt"})
        debate = Debate.objects.get()
        self.assertEqual(body["data"]["debateId"], str(debate.pk))
        self.assertEqual(body["data"]["status"], "DRAFT")
        self.assertTrue(body["data"]["createdAt"].endswith("Z"))

    def test_missing_required_fields(self):
        for field in ("categoryId", "kind", "title", "content"):
            body = self.body()
            del body[field]
            self.assert_invalid(body, field)

    def test_invalid_text_and_kind(self):
        self.assert_invalid(self.body(title="a" * 101), "title")
        self.assert_invalid(self.body(title="   "), "title")
        self.assert_invalid(self.body(content=" \n\t"), "content")
        self.assert_invalid(self.body(kind="LONG"), "kind")

    def test_invalid_duration(self):
        quick = self.body()
        del quick["durationMinutes"]
        self.assert_invalid(quick, "durationMinutes")
        self.assert_invalid(self.body(durationMinutes=None), "durationMinutes")
        self.assert_invalid(self.body(durationMinutes=4), "durationMinutes")
        self.assert_invalid(self.body(durationMinutes=16), "durationMinutes")
        self.assert_invalid(self.body(durationMinutes=True), "durationMinutes")
        self.assert_invalid(self.body(durationMinutes="10"), "durationMinutes")
        self.assert_invalid(self.body(kind="ENDLESS", durationMinutes=10), "durationMinutes")

    def test_invalid_category_id(self):
        for value in (self.category.pk, "abc", "0", str(2**63)):
            self.assert_invalid(self.body(categoryId=value), "categoryId")

    def test_category_not_found(self):
        inactive = Category.objects.create(name="Hidden", is_active=False)
        for category_id in ("999999", str(inactive.pk)):
            response = self.post(self.body(categoryId=category_id))
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "CATEGORY_NOT_FOUND")
        self.assertFalse(Debate.objects.exists())

    def test_requires_login(self):
        self.client.force_authenticate(None)
        response = self.post(self.body())

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_REQUIRED")

    def test_suspended_member_forbidden(self):
        self.user.status = User.Status.SUSPENDED
        self.user.save()
        response = self.post(self.body())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "FORBIDDEN")
        self.assertFalse(Debate.objects.exists())
