from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from debates.models import Category, Debate, DebateParticipant, Stance
from discussions.models import Opinion, OpinionReference
from engagement.models import Scrap


class BookmarkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("bookmark@example.com", "Member")
        cls.other = User.objects.create_user("bookmark-other@example.com", "Other")
        cls.debate = Debate.objects.create(
            creator=cls.user, category=Category.objects.create(name="Topic"),
            type=Debate.Type.ENDLESS, title="Debate", content="Topic", status=Debate.Status.OPEN,
        )
        cls.participant = DebateParticipant.objects.create(
            debate=cls.debate, user=cls.user, anonymous_no=1,
            initial_stance=Stance.PRO, current_stance=Stance.PRO,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.list_url = reverse("bookmark-list")

    def post(self):
        return Opinion.objects.create(
            debate=self.debate, participant=self.participant, content="Private after removal",
            content_plain="Private after removal", stance_at_write=Stance.PRO,
        )

    def bookmark_url(self, post):
        return reverse("post-bookmark", kwargs={"post_id": post.pk})

    def save(self, post):
        response = self.client.put(self.bookmark_url(post))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"data": {"bookmarked": True}})

    def test_own_opinion_repeated_save_and_remove_and_account_isolation(self):
        post = self.post()
        url = self.bookmark_url(post)
        other = Scrap.objects.create(user=self.other, target_type="OPINION", target_id=post.pk)
        self.save(post)
        self.save(post)
        self.assertEqual(Scrap.objects.filter(user=self.user, target_id=post.pk).count(), 1)
        for _ in range(2):
            response = self.client.delete(url)
            self.assertEqual(response.status_code, 204)
            self.assertEqual(response.content, b"")
        self.assertTrue(Scrap.objects.filter(pk=other.pk).exists())
        self.assertEqual(self.client.get(self.list_url).json(), {
            "data": {"posts": []}, "meta": {"nextCursor": None},
        })

    def test_deleted_and_filtered_are_retained_without_content_or_links(self):
        for state in (Opinion.Status.DELETED, Opinion.Status.FILTERED):
            post = self.post()
            self.save(post)
            OpinionReference.objects.create(opinion=post, url="https://example.org", safe_browsing_status="SAFE")
            Opinion.objects.filter(pk=post.pk).update(status=state)
            row = self.client.get(self.list_url).json()["data"]["posts"][0]
            self.assertEqual(set(row), {
                "postId", "debateId", "parentId", "rootId", "stanceAtWrite", "content", "status",
                "upvoteCount", "downvoteCount", "persuasionCount", "replyCount", "createdAt", "updatedAt",
                "anonymousNo", "references",
            })
            self.assertEqual(row["postId"], str(post.pk))
            self.assertEqual(row["status"], state)
            self.assertEqual(row["content"], "삭제된 글입니다")
            self.assertEqual(row["references"], [])
            self.assertTrue(row["createdAt"].endswith("Z"))
            self.assertEqual(self.client.put(self.bookmark_url(post)).json()["error"]["code"], "POST_NOT_FOUND")
            self.assertEqual(self.client.delete(self.bookmark_url(post)).status_code, 204)

    def test_bookmark_order_and_twenty_item_cursor(self):
        posts = [self.post() for _ in range(22)]
        for post in reversed(posts):
            self.save(post)
        # Other member and non-opinion rows never occupy this member's page.
        Scrap.objects.create(user=self.other, target_type="OPINION", target_id=posts[0].pk)
        Scrap.objects.create(user=self.user, target_type="DEBATE", target_id=self.debate.pk)
        first = self.client.get(self.list_url).json()
        self.assertEqual([row["postId"] for row in first["data"]["posts"]], [str(p.pk) for p in posts[:20]])
        cursor = first["meta"]["nextCursor"]
        second = self.client.get(self.list_url, {"cursor": cursor}).json()
        self.assertEqual([row["postId"] for row in second["data"]["posts"]], [str(p.pk) for p in posts[20:]])
        self.assertIsNone(second["meta"]["nextCursor"])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.list_url, {"cursor": cursor}).json()["error"]["code"], "CURSOR_INVALID")

    def test_requires_auth_and_rejects_missing_post_and_invalid_cursor(self):
        url = reverse("post-bookmark", kwargs={"post_id": 999999})
        response = self.client.put(url)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "POST_NOT_FOUND")
        response = self.client.get(self.list_url, {"cursor": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "CURSOR_INVALID")
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.list_url).status_code, 401)
        self.assertEqual(self.client.put(url).status_code, 401)
        self.assertEqual(self.client.delete(url).status_code, 401)
