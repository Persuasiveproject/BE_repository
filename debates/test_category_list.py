from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from debates.models import Category


class CategoryListTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("category-list")

    def get_categories(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["categories"]

    def test_orders_by_sort_order_then_id(self):
        late = Category.objects.create(name="Late", sort_order=2)
        tie_first = Category.objects.create(name="TieFirst", sort_order=1)
        tie_second = Category.objects.create(name="TieSecond", sort_order=1)

        ids = [item["id"] for item in self.get_categories()]

        self.assertEqual(ids, [str(tie_first.pk), str(tie_second.pk), str(late.pk)])

    def test_excludes_inactive_categories(self):
        active = Category.objects.create(name="Active")
        Category.objects.create(name="Inactive", is_active=False)

        ids = [item["id"] for item in self.get_categories()]

        self.assertEqual(ids, [str(active.pk)])

    def test_anonymous_and_authenticated_get_same_response(self):
        Category.objects.create(name="Politics")
        anonymous = self.client.get(self.url)
        self.client.force_authenticate(User.objects.create_user("member@example.com", "Member"))
        authenticated = self.client.get(self.url)

        self.assertEqual(anonymous.status_code, 200)
        self.assertEqual(authenticated.status_code, 200)
        self.assertEqual(anonymous.json(), authenticated.json())

    def test_response_keys_and_types(self):
        category = Category.objects.create(name="Economy", sort_order=3)

        body = self.client.get(self.url).json()

        self.assertEqual(set(body), {"data"})
        self.assertEqual(set(body["data"]), {"categories"})
        item = body["data"]["categories"][0]
        self.assertEqual(set(item), {"id", "name", "sortOrder"})
        self.assertEqual(item, {"id": str(category.pk), "name": "Economy", "sortOrder": 3})
        self.assertIsInstance(item["id"], str)
        self.assertIs(type(item["sortOrder"]), int)

    def test_empty_list(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"data": {"categories": []}})

    def test_post_not_allowed(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["error"]["code"], "METHOD_NOT_ALLOWED")
