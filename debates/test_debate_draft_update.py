from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from debates.models import Category, Debate


DRAFT_FIELDS = {
    "debateId", "kind", "title", "status", "createdAt", "categoryId", "content", "durationMinutes",
}


class DebateDraftUpdateTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user("writer@example.com", "Writer")
        self.category = Category.objects.create(name="Politics")
        self.debate = Debate.objects.create(
            creator=self.user, category=self.category, type="QUICK",
            title="Title", content="Content", duration_minutes=10,
        )
        self.url = reverse("debate-detail", args=[self.debate.pk])
        self.client.force_authenticate(self.user)

    def patch(self, body, url=None):
        return self.client.patch(url or self.url, body, format="json")

    def assert_unchanged(self):
        self.debate.refresh_from_db()
        self.assertEqual(
            (self.debate.category_id, self.debate.type, self.debate.title,
             self.debate.content, self.debate.duration_minutes),
            (self.category.pk, "QUICK", "Title", "Content", 10),
        )

    def assert_error(self, body, status, code):
        response = self.patch(body)
        self.assertEqual(response.status_code, status, body)
        self.assertEqual(response.json()["error"]["code"], code)
        self.assert_unchanged()
        return response

    def test_updates_title_only(self):
        response = self.patch({"title": "New title"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"data"})
        self.assertEqual(set(body["data"]), {"draft"})
        draft = body["data"]["draft"]
        self.assertEqual(set(draft), DRAFT_FIELDS)
        self.assertEqual(draft["debateId"], str(self.debate.pk))
        self.assertEqual(draft["categoryId"], str(self.category.pk))
        self.assertEqual(draft["kind"], "QUICK")
        self.assertEqual(draft["title"], "New title")
        self.assertEqual(draft["content"], "Content")
        self.assertEqual(draft["status"], "DRAFT")
        self.assertIs(type(draft["durationMinutes"]), int)
        self.assertEqual(draft["durationMinutes"], 10)
        self.assertTrue(draft["createdAt"].endswith("Z"))
        self.debate.refresh_from_db()
        self.assertEqual(self.debate.title, "New title")
        self.assertEqual(self.debate.content, "Content")

    def test_updates_all_fields(self):
        other = Category.objects.create(name="Economy")
        response = self.patch({
            "categoryId": str(other.pk), "kind": "ENDLESS", "title": "T2",
            "content": "C2", "durationMinutes": None,
        })

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["data"]["draft"]["durationMinutes"])
        self.debate.refresh_from_db()
        self.assertEqual(
            (self.debate.category_id, self.debate.type, self.debate.title,
             self.debate.content, self.debate.duration_minutes),
            (other.pk, "ENDLESS", "T2", "C2", None),
        )

    def test_empty_body_keeps_draft(self):
        response = self.patch({})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["draft"]["title"], "Title")
        self.assert_unchanged()

    def test_kind_switch_checks_final_combination(self):
        self.assert_error({"kind": "ENDLESS"}, 400, "DRAFT_INVALID")
        self.assertEqual(self.patch({"kind": "ENDLESS", "durationMinutes": None}).status_code, 200)

        self.assertEqual(self.patch({"kind": "QUICK"}).status_code, 400)
        self.assertEqual(self.patch({"kind": "QUICK", "durationMinutes": 10}).status_code, 200)
        self.assert_unchanged()

    def test_duration_only(self):
        self.assert_error({"durationMinutes": None}, 400, "DRAFT_INVALID")
        self.assertEqual(self.patch({"durationMinutes": 7}).status_code, 200)
        self.debate.refresh_from_db()
        self.assertEqual(self.debate.duration_minutes, 7)

        self.debate.type, self.debate.duration_minutes = "ENDLESS", None
        self.debate.save()
        response = self.patch({"durationMinutes": 10})
        self.assertEqual(response.status_code, 400)
        self.assertIn("durationMinutes", response.json()["error"]["details"])

    def test_explicit_null_rejected(self):
        for field in ("categoryId", "kind", "title", "content"):
            response = self.assert_error({field: None}, 400, "DRAFT_INVALID")
            self.assertIn(field, response.json()["error"]["details"])

    def test_category_not_found(self):
        inactive = Category.objects.create(name="Hidden", is_active=False)
        for category_id in ("999999", str(inactive.pk)):
            self.assert_error({"categoryId": category_id}, 404, "CATEGORY_NOT_FOUND")

    def test_other_member_forbidden(self):
        self.client.force_authenticate(User.objects.create_user("other@example.com", "Other"))

        self.assert_error({"title": "Hijack"}, 403, "NOT_DEBATE_CREATOR")

    def test_non_draft_conflict(self):
        Debate.objects.filter(pk=self.debate.pk).update(status=Debate.Status.OPEN)

        self.assert_error({"title": "Late"}, 409, "NOT_DRAFT")

    def test_missing_debate(self):
        response = self.patch({"title": "X"}, url=reverse("debate-detail", args=[999999]))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")

    def test_requires_login(self):
        self.client.force_authenticate(None)

        self.assert_error({"title": "X"}, 401, "AUTHENTICATION_REQUIRED")
