from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from engagement.models import Notification


ITEM_FIELDS = {
    "notificationId", "type", "title", "body", "targetType", "targetId", "isRead", "createdAt",
}


class NotificationListTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("notification-list")
        self.user = User.objects.create_user("member@example.com", "Member")
        self.client.force_authenticate(self.user)

    def notify(self, user=None, **fields):
        values = {"user": user or self.user, "type": "REPLY", "title": "New reply"}
        values.update(fields)
        return Notification.objects.create(**values)

    def get(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def ids(self, body):
        return [item["notificationId"] for item in body["data"]["notifications"]]

    def test_response_shape(self):
        notification = self.notify(
            type="DEBATE_CLOSED", body="Your debate closed.", target_type="DEBATE", target_id=7,
        )

        body = self.get()

        self.assertEqual(set(body), {"data", "meta"})
        self.assertEqual(set(body["data"]), {"notifications", "unreadCount"})
        self.assertEqual(body["meta"], {"nextCursor": None})
        item = body["data"]["notifications"][0]
        self.assertEqual(set(item), ITEM_FIELDS)
        self.assertEqual(item["notificationId"], str(notification.pk))
        self.assertEqual(item["type"], "DEBATE_CLOSED")
        self.assertEqual(item["title"], "New reply")
        self.assertEqual(item["body"], "Your debate closed.")
        self.assertEqual(item["targetType"], "DEBATE")
        self.assertEqual(item["targetId"], "7")
        self.assertIs(item["isRead"], False)
        self.assertTrue(item["createdAt"].endswith("Z"))
        self.assertIs(type(body["data"]["unreadCount"]), int)

    def test_nullable_fields_are_null(self):
        self.notify()

        item = self.get()["data"]["notifications"][0]

        self.assertIsNone(item["body"])
        self.assertIsNone(item["targetType"])
        self.assertIsNone(item["targetId"])

    def test_only_own_notifications_newest_first(self):
        other = User.objects.create_user("other@example.com", "Other")
        self.notify(user=other)
        first = self.notify()
        second = self.notify(is_read=True)

        body = self.get()

        self.assertEqual(self.ids(body), [str(second.pk), str(first.pk)])
        self.assertEqual(body["data"]["unreadCount"], 1)

    def test_unread_only_filter(self):
        unread = self.notify()
        read = self.notify(is_read=True)

        only_unread = self.get(unreadOnly="true")
        self.assertEqual(self.ids(only_unread), [str(unread.pk)])
        self.assertEqual(only_unread["data"]["unreadCount"], 1)
        self.assertEqual(self.ids(self.get(unreadOnly="false")), [str(read.pk), str(unread.pk)])
        self.assertEqual(self.ids(self.get()), [str(read.pk), str(unread.pk)])

    def test_unread_count_covers_all_pages(self):
        for index in range(21):
            self.notify(is_read=index % 3 == 0)
        unread = Notification.objects.filter(is_read=False).count()

        first = self.get()
        self.assertEqual(len(first["data"]["notifications"]), 20)
        self.assertIsNotNone(first["meta"]["nextCursor"])
        self.assertEqual(first["data"]["unreadCount"], unread)

        second = self.get(cursor=first["meta"]["nextCursor"])
        self.assertEqual(len(second["data"]["notifications"]), 1)
        self.assertIsNone(second["meta"]["nextCursor"])
        self.assertEqual(second["data"]["unreadCount"], unread)

    def test_invalid_query(self):
        response = self.client.get(self.url, {"unreadOnly": "yes"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "FILTER_INVALID")

        response = self.client.get(self.url, {"cursor": "not-a-cursor"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "CURSOR_INVALID")

    def test_requires_login(self):
        self.client.force_authenticate(None)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_REQUIRED")
