from datetime import timedelta

from django.test import TestCase

from accounts.models import User, UserIdentityVerification
from debates.test_b_api import DebateBFixture
from engagement.models import Notification


class ReplyNotificationTests(DebateBFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.join()
        self.parent = self.write("parent", "Parent").json()["data"]["postId"]
        self.replier = self.member_named("replier@example.com", "Replier")

    def member_named(self, email, nickname):
        user = User.objects.create_user(email, nickname)
        UserIdentityVerification.objects.create(
            user=user, provider="test", request_id=f"reply-fixture-{user.pk}", status="VERIFIED",
            identity_key_hash=f"{user.pk:064x}", verified_at=self.now,
            expires_at=self.now + timedelta(minutes=5),
        )
        self.as_user(user)
        self.assertEqual(self.join(key=f"join-{email}").status_code, 201)
        self.as_user(self.member)
        return user

    def as_user(self, user):
        self.client.force_authenticate(user=user)

    def reply(self, user, key, parent=None):
        self.as_user(user)
        response = self.write(key, f"Reply {key}", parentId=parent or self.parent)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["data"]["postId"]

    def notifications(self, user=None):
        return list(Notification.objects.filter(user=user or self.member).order_by("id"))

    def test_reply_to_other_users_post_notifies_author(self):
        self.reply(self.replier, "r1")

        [notification] = self.notifications()
        self.assertEqual(notification.type, Notification.Type.REPLY)
        self.assertEqual(notification.title, "내 글에 답글이 달렸어요")
        self.assertEqual(notification.body, "답글 1개")
        self.assertEqual((notification.target_type, notification.target_id), ("OPINION", int(self.parent)))
        self.assertFalse(Notification.objects.filter(user=self.replier).exists())

    def test_reply_to_own_post_has_no_notification(self):
        self.reply(self.member, "self")

        self.assertFalse(Notification.objects.exists())

    def test_replies_within_window_are_bundled(self):
        for key in ("r1", "r2", "r3"):
            self.reply(self.replier, key)

        [notification] = self.notifications()
        self.assertEqual(notification.body, "답글 3개")

    def test_own_replies_are_not_counted_in_bundle(self):
        self.reply(self.replier, "r1")
        self.reply(self.member, "self")
        self.reply(self.replier, "r2")

        [notification] = self.notifications()
        self.assertEqual(notification.body, "답글 2개")

    def test_replies_from_different_users_are_bundled(self):
        other = self.member_named("other@example.com", "Other")
        self.reply(self.replier, "r1")
        self.reply(other, "o1")

        [notification] = self.notifications()
        self.assertEqual(notification.body, "답글 2개")

    def test_reply_after_window_creates_new_notification(self):
        self.reply(self.replier, "r1")
        Notification.objects.update(created_at=self.now - timedelta(seconds=61))
        self.reply(self.replier, "r2")

        first, second = self.notifications()
        self.assertEqual((first.body, second.body), ("답글 1개", "답글 1개"))

    def test_reply_after_read_creates_new_notification(self):
        self.reply(self.replier, "r1")
        Notification.objects.update(is_read=True)
        self.reply(self.replier, "r2")

        first, second = self.notifications()
        self.assertEqual((first.body, first.is_read), ("답글 1개", True))
        self.assertEqual((second.body, second.is_read), ("답글 1개", False))

    def test_nested_reply_notifies_direct_parent_author(self):
        child = self.reply(self.replier, "r1")
        Notification.objects.all().delete()
        self.reply(self.member, "nested", parent=child)

        [notification] = self.notifications(self.replier)
        self.assertEqual(notification.target_id, int(child))
        self.assertFalse(Notification.objects.filter(user=self.member).exists())

    def test_idempotent_retry_does_not_add_notification(self):
        self.reply(self.replier, "r1")
        self.reply(self.replier, "r1")

        [notification] = self.notifications()
        self.assertEqual(notification.body, "답글 1개")
