from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User, UserInterestCategory
from debates.models import Category


class MeInterestsTests(TestCase):
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

    def test_put_saves_and_replaces_up_to_five_categories(self):
        selected = [self.categories[0], self.categories[2], self.categories[4]]
        first = self.put({"categoryIds": [str(c.pk) for c in selected]})
        self.assertEqual(first.status_code, 200)
        expected = [
            {"id": str(c.pk), "name": c.name, "sortOrder": c.sort_order}
            for c in sorted(selected, key=lambda c: (c.sort_order, c.pk))
        ]
        self.assertEqual(first.json(), {"data": {"interests": expected}})
        self.assertEqual(self.client.get(reverse("me")).json()["data"]["interests"], expected)

        five = self.categories[:5]
        second = self.put({"categoryIds": [str(c.pk) for c in five]})
        self.assertEqual(second.status_code, 200)
        expected_five = [
            {"id": str(c.pk), "name": c.name, "sortOrder": c.sort_order}
            for c in sorted(five, key=lambda c: (c.sort_order, c.pk))
        ]
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
        self.assertEqual(suspended.json()["error"]["code"], "FORBIDDEN")
