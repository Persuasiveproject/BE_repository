from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from accounts.models import User

from .models import Category, Debate, DebateParticipant, Stance


class JoinedDebateListTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("member@example.com", "Member")
        self.other = User.objects.create_user("other@example.com", "Other")
        self.category = Category.objects.create(name="Society")
        self.client = APIClient()
        self.url = reverse("joined-debate-list")

    def join_debate(self, user, status=Debate.Status.OPEN):
        debate = Debate.objects.create(
            creator=self.other,
            category=self.category,
            type=Debate.Type.ENDLESS,
            title="Debate",
            content="Topic",
            started_at=timezone.now(),
            status=status,
        )
        DebateParticipant.objects.create(
            debate=debate,
            user=user,
            anonymous_no=1,
            initial_stance=Stance.PRO,
            current_stance=Stance.NEUTRAL,
        )
        return debate

    def test_requires_authentication(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_filters_status_and_returns_only_my_participation(self):
        open_debate = self.join_debate(self.user)
        self.join_debate(self.user, Debate.Status.CLOSED)
        self.join_debate(self.other)
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.url, {"status": "OPEN"})
        self.assertEqual(response.status_code, 200)
        item, = response.data["data"]["debates"]
        self.assertEqual(item["id"], str(open_debate.pk))
        self.assertEqual(item["kind"], "ENDLESS")
        self.assertEqual(item["status"], "OPEN")
        self.assertEqual(item["currentStance"], "NEUTRAL")
        error = self.client.get(self.url, {"status": "bad"})
        self.assertEqual(error.status_code, 400)
        self.assertEqual(error.data["error"]["code"], "FILTER_INVALID")

    def test_paginates_twenty_items_without_duplicates(self):
        ids = [self.join_debate(self.user).id for _ in range(21)]
        self.client.force_authenticate(user=self.user)

        first = self.client.get(self.url)
        second = self.client.get(self.url, {"cursor": first.data["meta"]["nextCursor"]})
        self.assertEqual(
            [item["id"] for item in first.data["data"]["debates"]],
            [str(pk) for pk in ids[:0:-1]],
        )
        self.assertEqual([item["id"] for item in second.data["data"]["debates"]], [str(ids[0])])
        self.assertIsNone(second.data["meta"]["nextCursor"])

    def test_empty_list(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"data": {"debates": []}, "meta": {"nextCursor": None}})

    def test_expired_open_debate_appears_in_closed_filter_before_worker_runs(self):
        debate = self.join_debate(self.user)
        deadline = timezone.now()
        Debate.objects.filter(pk=debate.pk).update(ends_at=deadline)
        self.client.force_authenticate(user=self.user)

        opened = self.client.get(self.url, {"status": "OPEN"})
        closed = self.client.get(self.url, {"status": "CLOSED"})
        self.assertEqual(opened.data["data"]["debates"], [])
        item, = closed.data["data"]["debates"]
        self.assertEqual(item["id"], str(debate.pk))
        self.assertEqual(item["status"], "CLOSED")
        self.assertEqual(parse_datetime(item["closedAt"]), deadline)

    def test_legacy_open_debate_without_deadline_remains_open(self):
        debate = self.join_debate(self.user)
        Debate.objects.filter(pk=debate.pk).update(started_at=None)
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.url, {"status": "OPEN"})
        item, = response.data["data"]["debates"]
        self.assertEqual(item["id"], str(debate.pk))
        self.assertEqual(item["status"], "OPEN")
        self.assertIsNone(item["endsAt"])

    def test_malformed_cursor_returns_filter_error(self):
        self.join_debate(self.user)
        self.client.force_authenticate(user=self.user)

        for cursor in ("", "%%%", "cD1hYmM=", "cD0tMQ==", "bz0xJnI9MQ=="):
            with self.subTest(cursor=cursor):
                response = self.client.get(self.url, {"cursor": cursor})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data["error"]["code"], "FILTER_INVALID")

    def test_excludes_drafts_and_canceled_debates(self):
        self.join_debate(self.user, Debate.Status.DRAFT)
        self.join_debate(self.user, Debate.Status.CANCELED)
        self.client.force_authenticate(user=self.user)

        self.assertEqual(self.client.get(self.url).data["data"]["debates"], [])
        self.assertEqual(self.client.get(self.url, {"status": "DRAFT"}).status_code, 400)
