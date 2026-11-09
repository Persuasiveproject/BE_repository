from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from debates.models import Category, Debate


ITEM_FIELDS = {
    "id", "kind", "title", "status", "createdAt", "categoryId", "participantCount",
    "opinionCount", "viewCount", "startedAt", "endsAt", "closedAt", "canceledAt",
}


class DebateListTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("debate-list")
        self.now = timezone.now()
        self.user = User.objects.create_user("writer@example.com", "Writer")
        self.category = Category.objects.create(name="Politics")
        self.other_category = Category.objects.create(name="Economy")

    def debate(self, status="OPEN", **fields):
        values = {
            "creator": self.user, "category": self.category, "type": "QUICK",
            "title": "Title", "content": "Content", "duration_minutes": 10, "status": status,
        }
        if status == "OPEN":
            values["started_at"] = self.now
        values.update(fields)
        return Debate.objects.create(**values)

    def ids(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200, response.content)
        return [item["id"] for item in response.json()["data"]["debates"]]

    def assert_invalid(self, code, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 400, params)
        self.assertEqual(response.json()["error"]["code"], code)

    def test_lists_open_and_closed_newest_first(self):
        self.debate(status="DRAFT")
        self.debate(status="CANCELED", canceled_at=self.now)
        closed = self.debate(status="CLOSED", started_at=self.now - timedelta(hours=1), closed_at=self.now)
        opened = self.debate()

        self.assertEqual(self.ids(), [str(opened.pk), str(closed.pk)])

    def test_response_shape(self):
        debate = self.debate()

        body = self.client.get(self.url).json()

        self.assertEqual(set(body), {"data", "meta"})
        self.assertEqual(set(body["data"]), {"debates"})
        self.assertEqual(body["meta"], {"nextCursor": None})
        item = body["data"]["debates"][0]
        self.assertEqual(set(item), ITEM_FIELDS)
        self.assertEqual(item["id"], str(debate.pk))
        self.assertEqual(item["categoryId"], str(self.category.pk))
        self.assertEqual(item["kind"], "QUICK")
        self.assertEqual(item["status"], "OPEN")
        for key in ("createdAt", "startedAt", "endsAt"):
            self.assertTrue(item[key].endswith("Z"), key)
        self.assertIsNone(item["closedAt"])
        self.assertIsNone(item["canceledAt"])

    def test_expired_open_is_closed(self):
        expired = self.debate(started_at=self.now - timedelta(hours=1))
        live = self.debate()

        response = self.client.get(self.url).json()["data"]["debates"]
        self.assertEqual({item["id"]: item["status"] for item in response},
                         {str(live.pk): "OPEN", str(expired.pk): "CLOSED"})
        self.assertEqual(self.ids(status="OPEN"), [str(live.pk)])
        self.assertEqual(self.ids(status="CLOSED"), [str(expired.pk)])

    def test_query_matches_title_case_insensitively(self):
        match = self.debate(title="Basic Income Debate")
        self.debate(title="Other")

        self.assertEqual(self.ids(query="  income "), [str(match.pk)])
        self.assertEqual(len(self.ids(query="   ")), 2)

    def test_category_and_kind_filters(self):
        endless = self.debate(type="ENDLESS", duration_minutes=None)
        other = self.debate(category=self.other_category)

        self.assertEqual(self.ids(kind="ENDLESS"), [str(endless.pk)])
        self.assertEqual(self.ids(categoryId=str(self.other_category.pk)), [str(other.pk)])

    def test_combined_filters(self):
        target = self.debate(type="ENDLESS", duration_minutes=None, category=self.other_category)
        self.debate(type="ENDLESS", duration_minutes=None)
        self.debate(category=self.other_category)
        self.debate(
            type="ENDLESS", duration_minutes=None, category=self.other_category,
            status="CLOSED", closed_at=self.now,
        )

        ids = self.ids(status="OPEN", categoryId=str(self.other_category.pk), kind="ENDLESS")
        self.assertEqual(ids, [str(target.pk)])

    def test_pages_of_20(self):
        debates = [self.debate() for _ in range(21)]

        first = self.client.get(self.url).json()
        self.assertEqual(len(first["data"]["debates"]), 20)
        self.assertIsNotNone(first["meta"]["nextCursor"])

        second = self.client.get(self.url, {"cursor": first["meta"]["nextCursor"]}).json()
        self.assertEqual([item["id"] for item in second["data"]["debates"]], [str(debates[0].pk)])
        self.assertIsNone(second["meta"]["nextCursor"])

    def test_invalid_filters(self):
        for params in (
            {"status": "DRAFT"}, {"kind": "LONG"}, {"sort": "popular"},
            {"categoryId": "abc"}, {"categoryId": "0"}, {"categoryId": "999999"},
            {"query": "a" * 101},
        ):
            self.assert_invalid("FILTER_INVALID", **params)

    def test_invalid_cursor(self):
        self.assert_invalid("CURSOR_INVALID", cursor="not-a-cursor")

    def test_sort_latest_allowed(self):
        self.debate()
        self.assertEqual(len(self.ids(sort="latest")), 1)

    def test_anonymous_and_member_allowed(self):
        self.debate()
        self.assertEqual(len(self.ids()), 1)
        self.client.force_authenticate(self.user)
        self.assertEqual(len(self.ids()), 1)
