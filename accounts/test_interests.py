from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User, UserInterestCategory
from debates.models import Category


class MeInterestsFixture:
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("me-interests")
        self.user = User.objects.create_user("interests@example.com", "Member")
        self.other = User.objects.create_user("other-interests@example.com", "Other")
        self.categories = [
            Category.objects.create(name=f"Cat {i}", sort_order=10 - i)
            for i in range(6)
        ]
        self.inactive = Category.objects.create(name="Inactive", sort_order=0, is_active=False)
        self.client.force_authenticate(self.user)

    def put(self, body):
        return self.client.put(self.url, body, format="json")

    @staticmethod
    def expected_interests(categories):
        return [
            {"id": str(c.pk), "name": c.name, "sortOrder": c.sort_order}
            for c in sorted(categories, key=lambda c: (c.sort_order, c.pk))
        ]


class MeInterestsTests(MeInterestsFixture, TestCase):
    def test_put_saves_and_replaces_up_to_five_categories(self):
        selected = [self.categories[0], self.categories[2], self.categories[4]]
        first = self.put({"categoryIds": [str(c.pk) for c in selected]})
        self.assertEqual(first.status_code, 200)
        expected = self.expected_interests(selected)
        self.assertEqual(first.json(), {"data": {"interests": expected}})
        self.assertEqual(self.client.get(reverse("me")).json()["data"]["interests"], expected)

        five = self.categories[:5]
        second = self.put({"categoryIds": [str(c.pk) for c in five]})
        self.assertEqual(second.status_code, 200)
        expected_five = self.expected_interests(five)
        self.assertEqual(second.json(), {"data": {"interests": expected_five}})

        cleared = self.put({"categoryIds": []})
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.json(), {"data": {"interests": []}})
        self.assertFalse(UserInterestCategory.objects.filter(user=self.user).exists())

    def test_put_deduplicates_ids_within_five(self):
        c1, c2 = self.categories[0], self.categories[1]
        response = self.put({"categoryIds": [str(c1.pk), str(c1.pk), str(c2.pk)]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["data"]["interests"]), 2)
        self.assertEqual(UserInterestCategory.objects.filter(user=self.user).count(), 2)

    def test_put_does_not_modify_other_users_interests(self):
        UserInterestCategory.objects.create(user=self.other, category=self.categories[0])
        response = self.put({"categoryIds": [str(self.categories[1].pk)]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(UserInterestCategory.objects.filter(user=self.other).values_list("category_id", flat=True)),
            [self.categories[0].pk],
        )

    def test_put_accepts_more_than_five_entries_with_at_most_five_unique_ids(self):
        for selected in (self.categories[:1], self.categories[:5]):
            with self.subTest(unique_count=len(selected)):
                ids = [str(c.pk) for c in selected]
                response = self.put({"categoryIds": ids * 6})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {"data": {"interests": self.expected_interests(selected)}},
                )
                self.assertEqual(
                    set(UserInterestCategory.objects.filter(user=self.user).values_list("category_id", flat=True)),
                    {c.pk for c in selected},
                )

    def test_put_rejects_more_than_five_unique_ids_even_with_duplicates(self):
        UserInterestCategory.objects.create(user=self.user, category=self.categories[0])
        response = self.put({"categoryIds": [str(c.pk) for c in self.categories] * 2})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "INTEREST_LIMIT_EXCEEDED")
        self.assertEqual(
            list(UserInterestCategory.objects.filter(user=self.user).values_list("category_id", flat=True)),
            [self.categories[0].pk],
        )

    def test_exceeding_limit_returns_400_without_modifying_db(self):
        UserInterestCategory.objects.create(user=self.user, category=self.categories[0])

        response = self.put({"categoryIds": [str(c.pk) for c in self.categories]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "INTEREST_LIMIT_EXCEEDED")
        self.assertEqual(
            list(UserInterestCategory.objects.filter(user=self.user).values_list("category_id", flat=True)),
            [self.categories[0].pk],
        )

    def test_missing_or_inactive_category_returns_404_without_modifying_db(self):
        UserInterestCategory.objects.create(user=self.user, category=self.categories[0])

        for bad_id in ("999999", str(self.inactive.pk)):
            with self.subTest(bad_id=bad_id):
                response = self.put({"categoryIds": [str(self.categories[1].pk), bad_id]})
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["error"]["code"], "CATEGORY_NOT_FOUND")
                self.assertEqual(
                    list(UserInterestCategory.objects.filter(user=self.user).values_list("category_id", flat=True)),
                    [self.categories[0].pk],
                )

    def test_invalid_payloads_return_400_without_modifying_db(self):
        UserInterestCategory.objects.create(user=self.user, category=self.categories[0])

        for payload in (
            {},
            {"categoryIds": [], "extra": 1},
            {"categoryIds": None},
            {"categoryIds": "1"},
            {"categoryIds": [self.categories[0].pk]},
            {"categoryIds": ["0"]},
            {"categoryIds": ["abc"]},
            {"categoryIds": [str(2**63)]},
            {"categoryIds": [None]},
            {"categoryIds": [str(self.categories[0].pk)] * 6 + [None]},
            [],
        ):
            with self.subTest(payload=payload):
                response = self.put(payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
                self.assertEqual(
                    list(UserInterestCategory.objects.filter(user=self.user).values_list("category_id", flat=True)),
                    [self.categories[0].pk],
                )

    def test_requires_active_member(self):
        self.client.force_authenticate(None)
        anon = self.put({"categoryIds": []})
        self.assertEqual(anon.status_code, 401)
        self.assertEqual(anon.json()["error"]["code"], "AUTHENTICATION_REQUIRED")

        self.user.status = User.Status.SUSPENDED
        self.user.save(update_fields=["status"])
        self.client.force_authenticate(self.user)
        suspended = self.put({"categoryIds": []})
        self.assertEqual(suspended.status_code, 403)
        self.assertEqual(suspended.json()["error"]["code"], "ACCOUNT_SUSPENDED")


class MeInterestsConcurrencyTests(MeInterestsFixture, TransactionTestCase):
    def put_concurrently(self, payloads):
        ready = Barrier(len(payloads))

        def submit(category_ids):
            try:
                connections.close_all()
                client = APIClient()
                client.force_authenticate(user=self.user)
                ready.wait(timeout=10)
                response = client.put(self.url, {"categoryIds": category_ids}, format="json")
                return response.status_code, response.json()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(payloads)) as executor:
            return list(executor.map(submit, payloads))

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_identical_puts_from_empty_state_succeed(self):
        selected = [self.categories[0], self.categories[2], self.categories[4]]
        ids = [str(c.pk) for c in selected]
        responses = self.put_concurrently([ids, ids, ids])

        expected = self.expected_interests(selected)
        for status, body in responses:
            self.assertEqual(status, 200)
            self.assertEqual(body, {"data": {"interests": expected}})

        self.assertEqual(
            set(UserInterestCategory.objects.filter(user=self.user).values_list("category_id", flat=True)),
            {c.pk for c in selected},
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_disjoint_puts_serialize_without_exceeding_limit(self):
        first_set = self.categories[:3]
        second_set = self.categories[3:]
        responses = self.put_concurrently([
            [str(c.pk) for c in first_set],
            [str(c.pk) for c in second_set],
        ])

        expected_first = self.expected_interests(first_set)
        expected_second = self.expected_interests(second_set)
        self.assertEqual(responses[0], (200, {"data": {"interests": expected_first}}))
        self.assertEqual(responses[1], (200, {"data": {"interests": expected_second}}))

        stored_ids = set(
            UserInterestCategory.objects.filter(user=self.user).values_list("category_id", flat=True)
        )
        self.assertEqual(len(stored_ids), 3)
        self.assertIn(stored_ids, [{c.pk for c in first_set}, {c.pk for c in second_set}])
