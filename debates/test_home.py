from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User, UserInterestCategory
from debates.models import Category, Debate, DebateParticipant, Stance


ITEM_FIELDS = {
    "id", "kind", "title", "status", "createdAt", "categoryId", "participantCount",
    "opinionCount", "viewCount", "startedAt", "endsAt", "closedAt", "canceledAt",
}


class HomeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("home")
        self.now = timezone.now()
        self.user = User.objects.create_user("member@example.com", "Member")
        self.category = Category.objects.create(name="Politics")
        self.interest = Category.objects.create(name="Economy")
        self.joiners = 0

    def debate(self, status="OPEN", **fields):
        values = {
            "creator": self.user, "category": self.category, "type": "QUICK",
            "title": "Title", "content": "Content", "duration_minutes": 10, "status": status,
        }
        if status == "OPEN":
            values["started_at"] = self.now
        values.update(fields)
        return Debate.objects.create(**values)

    def join(self, debate, hours_ago):
        self.joiners += 1
        user = User.objects.create_user(f"joiner{self.joiners}@example.com", f"Joiner{self.joiners}")
        participant = DebateParticipant.objects.create(
            debate=debate, user=user, anonymous_no=self.joiners,
            initial_stance=Stance.PRO, current_stance=Stance.PRO,
        )
        DebateParticipant.objects.filter(pk=participant.pk).update(
            joined_at=self.now - timedelta(hours=hours_ago),
        )

    def home(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def ids(self, key):
        return [item["id"] for item in self.home()["data"][key]]

    def test_response_shape(self):
        self.debate()

        body = self.home()

        self.assertEqual(set(body), {"data"})
        self.assertEqual(set(body["data"]), {"popularDebates", "recommendedDebates"})
        for key in ("popularDebates", "recommendedDebates"):
            self.assertEqual(set(body["data"][key][0]), ITEM_FIELDS)

    def test_only_live_open_debates(self):
        self.debate(status="DRAFT")
        self.debate(status="CANCELED", canceled_at=self.now)
        self.debate(status="CLOSED", started_at=self.now - timedelta(hours=1), closed_at=self.now)
        self.debate(started_at=self.now - timedelta(hours=1))
        live = self.debate()

        self.assertEqual(self.ids("popularDebates"), [str(live.pk)])
        self.assertEqual(self.ids("recommendedDebates"), [str(live.pk)])

    def test_popular_counts_recent_joins(self):
        older_tie = self.debate()
        busy = self.debate()
        newer_tie = self.debate()
        stale = self.debate()
        for _ in range(2):
            self.join(busy, hours_ago=1)
        self.join(older_tie, hours_ago=23)
        self.join(newer_tie, hours_ago=1)
        for _ in range(3):
            self.join(stale, hours_ago=25)

        self.assertEqual(
            self.ids("popularDebates"),
            [str(busy.pk), str(newer_tie.pk), str(older_tie.pk), str(stale.pk)],
        )

    def test_lists_are_limited_to_10(self):
        for _ in range(12):
            self.debate()

        data = self.home()["data"]

        self.assertEqual(len(data["popularDebates"]), 10)
        self.assertEqual(len(data["recommendedDebates"]), 10)

    def test_recommended_is_latest_for_anonymous(self):
        first = self.debate()
        second = self.debate(category=self.interest)

        self.assertEqual(self.ids("recommendedDebates"), [str(second.pk), str(first.pk)])

    def test_recommended_puts_interests_first(self):
        UserInterestCategory.objects.create(user=self.user, category=self.interest)
        liked = [self.debate(category=self.interest) for _ in range(3)]
        others = [self.debate() for _ in range(9)]
        self.client.force_authenticate(self.user)

        ids = self.ids("recommendedDebates")

        expected = [str(d.pk) for d in reversed(liked)] + [str(d.pk) for d in reversed(others)][:7]
        self.assertEqual(ids, expected)
        self.assertEqual(len(set(ids)), 10)

    def test_recommended_is_latest_without_interests(self):
        first = self.debate()
        second = self.debate(category=self.interest)
        self.client.force_authenticate(self.user)

        self.assertEqual(self.ids("recommendedDebates"), [str(second.pk), str(first.pk)])

    def test_empty(self):
        self.assertEqual(self.home(), {"data": {"popularDebates": [], "recommendedDebates": []}})
