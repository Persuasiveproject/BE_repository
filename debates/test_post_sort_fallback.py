from django.test import TestCase
from rest_framework.test import APIClient

from discussions.models import Opinion

from .models import DebateParticipant, Stance
from .test_b_api import DebateBFixture


class PersuasionSortFallbackTests(DebateBFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.join().status_code, 201)
        self.participant = DebateParticipant.objects.get(user=self.member)

    def make_posts(self, count, pro=None):
        # Upvotes rise while persuasion falls, so the two orders are reversed.
        return Opinion.objects.bulk_create([
            Opinion(
                debate=self.debate, participant=self.participant, content=f"Opinion {index}",
                content_plain=f"Opinion {index}", upvote_count=index, persuasion_count=count - index,
                stance_at_write=Stance.PRO if pro is None or index in pro else Stance.CON,
            )
            for index in range(count)
        ])

    def ids(self, **query):
        response = APIClient().get(self.posts_url, query)
        self.assertEqual(response.status_code, 200)
        return [post["postId"] for post in response.json()["data"]["posts"]]

    def by(self, posts, field):
        return [str(post.pk) for post in sorted(posts, key=lambda post: getattr(post, field), reverse=True)]

    def test_under_20_posts_uses_upvotes(self):
        posts = self.make_posts(19)

        self.assertEqual(self.ids(sort="persuasion"), self.by(posts, "upvote_count"))
        self.assertEqual(self.ids(sort="persuasion"), self.ids(sort="upvotes"))

    def test_20_posts_uses_persuasion(self):
        posts = self.make_posts(20)

        self.assertEqual(self.ids(sort="persuasion"), self.by(posts, "persuasion_count"))
        self.assertNotEqual(self.ids(sort="persuasion"), self.ids(sort="upvotes"))

    def test_replies_are_not_counted(self):
        posts = self.make_posts(19)
        parent = posts[0]
        Opinion.objects.bulk_create([
            Opinion(
                debate=self.debate, participant=self.participant, parent=parent, root_id=parent.pk,
                stance_at_write=Stance.PRO, content="Reply", content_plain="Reply",
            )
            for _ in range(5)
        ])

        self.assertEqual(self.ids(sort="persuasion"), self.by(posts, "upvote_count"))

    def test_count_ignores_stance_filter(self):
        posts = self.make_posts(20, pro={2, 9, 15})
        pro_posts = [post for post in posts if post.stance_at_write == Stance.PRO]

        self.assertEqual(self.ids(sort="persuasion", stance="PRO"), self.by(pro_posts, "persuasion_count"))

    def test_other_sorts_unchanged(self):
        for count in (5, 20):
            Opinion.objects.all().delete()
            posts = self.make_posts(count)
            with self.subTest(count=count):
                self.assertEqual(self.ids(sort="upvotes"), self.by(posts, "upvote_count"))
                self.assertEqual(self.ids(sort="latest"), [str(post.pk) for post in reversed(posts)])
