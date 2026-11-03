from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase


class JoinedDebatesContractTests(APITestCase):
    url = "/v1/me/debates"

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.member = user_model.objects.create(
            email="joined-member@example.test", nickname="Member"
        )
        cls.other = user_model.objects.create(
            email="joined-other@example.test", nickname="Other"
        )
        cls.category = apps.get_model("debates", "Category").objects.create(
            name="Joined debate contract"
        )

    def create_debate(self, title, status, participant, stance="PRO"):
        now = timezone.now()
        started_at = now - timedelta(hours=1 if status == "OPEN" else 74)
        debate = apps.get_model("debates", "Debate").objects.create(
            creator=self.other,
            category=self.category,
            type="ENDLESS",
            title=title,
            content="Contract fixture",
            status=status,
            started_at=started_at,
            ends_at=started_at + timedelta(hours=72),
            closed_at=None if status == "OPEN" else now - timedelta(hours=2),
            coin_cost=1,
            participant_count=1,
        )
        apps.get_model("debates", "DebateParticipant").objects.create(
            debate=debate,
            user=participant,
            anonymous_no=1,
            initial_stance="NEUTRAL",
            current_stance=stance,
        )
        return str(debate.pk)

    def get_page(self, **query):
        response = self.client.get(self.url, query)
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        return payload["data"]["debates"], payload["meta"]["nextCursor"]

    def test_requires_membership(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401, response.content)

    def test_filters_the_authenticated_members_debates_by_status(self):
        open_id = self.create_debate("My open debate", "OPEN", self.member)
        closed_id = self.create_debate("My closed debate", "CLOSED", self.member)
        self.create_debate("Other member's open debate", "OPEN", self.other)
        self.create_debate("Other member's closed debate", "CLOSED", self.other)

        self.client.force_authenticate(self.member)
        unfiltered, _ = self.get_page()
        self.assertCountEqual([item["id"] for item in unfiltered], [open_id, closed_id])
        for status, expected_id in (("OPEN", open_id), ("CLOSED", closed_id)):
            with self.subTest(status=status):
                page, next_cursor = self.get_page(status=status)
                self.assertEqual([item["id"] for item in page], [expected_id])
                self.assertEqual(page[0]["status"], status)
                self.assertIsNone(next_cursor)

    def test_rejects_invalid_status(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(self.url, {"status": "INVALID"})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"]["code"], "FILTER_INVALID")

    def test_cursor_advances_through_20_item_pages(self):
        expected_ids = [
            self.create_debate(f"Joined debate {index}", "OPEN", self.member)
            for index in range(21)
        ]
        self.create_debate("Other member's open debate", "OPEN", self.other)

        self.client.force_authenticate(self.member)
        first_page, cursor = self.get_page(status="OPEN")
        self.assertEqual(len(first_page), 20)
        self.assertIsInstance(cursor, str)
        self.assertTrue(cursor)

        second_page, next_cursor = self.get_page(status="OPEN", cursor=cursor)
        self.assertEqual(len(second_page), 1)
        self.assertIsNone(next_cursor)
        self.assertCountEqual(
            [item["id"] for item in first_page + second_page], expected_ids
        )
