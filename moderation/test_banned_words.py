from django.test import TestCase

from accounts.models import APIIdempotencyRecord
from debates.test_b_api import DebateBFixture
from discussions.models import Opinion
from moderation.checks import require_no_banned_words
from moderation.models import BannedWord


MESSAGE = "금지 표현이 포함되어 있어요. 수정해주세요"


class BannedWordPostTests(DebateBFixture, TestCase):
    def setUp(self):
        super().setUp()
        BannedWord.objects.create(word="Secret Word")
        BannedWord.objects.create(word="maskonly", level=BannedWord.Level.MASK)
        self.assertEqual(self.join().status_code, 201)

    def snapshot(self):
        self.debate.refresh_from_db()
        return (
            Opinion.objects.count(), self.debate.opinion_count,
            list(Opinion.objects.order_by("id").values_list("reply_count", flat=True)),
            APIIdempotencyRecord.objects.count(),
        )

    def assert_blocked(self, response):
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": {"code": "BANNED_WORD", "message": MESSAGE}})
        text = response.content.decode().lower()
        self.assertNotIn("secret", text)

    def test_blocks_banned_word_without_side_effects(self):
        before = self.snapshot()

        for index, content in enumerate((
            "this has a secret word inside",
            "SECRET WORD",
            "sec ret\nwo\trd",
        )):
            self.assert_blocked(self.write(f"bad-{index}", content))

        self.assertEqual(self.snapshot(), before)

    def test_mask_word_and_clean_text_pass(self):
        response = self.write("mask", "maskonly is fine for now")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.write("clean", "A normal reason").status_code, 201)
        self.debate.refresh_from_db()
        self.assertEqual(self.debate.opinion_count, 2)

    def test_reply_is_checked(self):
        parent = self.write("parent", "Root reason").json()["data"]["postId"]
        before = self.snapshot()

        self.assert_blocked(self.write("reply", "a secretword reply", parentId=parent))

        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.write("reply-ok", "fine reply", parentId=parent).status_code, 201)


class RequireNoBannedWordsTests(TestCase):
    def test_skips_empty_values_and_blank_words(self):
        BannedWord.objects.create(word="   ")

        require_no_banned_words(None, "", "anything")

    def test_checks_every_text(self):
        BannedWord.objects.create(word="바보")

        with self.assertRaises(Exception) as raised:
            require_no_banned_words("제목은 괜찮음", "내용에 바 보 포함")
        self.assertEqual(raised.exception.error["code"], "BANNED_WORD")
