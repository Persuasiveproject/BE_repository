from django.test import SimpleTestCase, TestCase

from discussions.models import Opinion
from moderation.models import BannedWord

from .api.posts.formatting import format_content
from .test_b_api import DebateBFixture


class FormatContentTests(SimpleTestCase):
    def assert_html(self, raw, expected):
        self.assertEqual(format_content(raw)[0], expected)

    def test_allowed_tags_are_kept(self):
        self.assert_html("<p><strong>굵게</strong> <em>기울임</em></p>", "<p><strong>굵게</strong> <em>기울임</em></p>")
        self.assert_html("<ul><li>a</li></ul><ol><li>b<br>c</li></ol>", "<ul><li>a</li></ul><ol><li>b<br>c</li></ol>")

    def test_attributes_are_dropped(self):
        self.assert_html('<p onclick="x" class="a">hi</p>', "<p>hi</p>")

    def test_script_style_content_and_unknown_tags_are_dropped(self):
        self.assert_html("a<script>alert(1)</script><style>p{}</style>b", "ab")
        self.assert_html('<iframe src="x">in</iframe><img src="y" onerror="z">', "in")

    def test_links_are_never_created(self):
        self.assert_html('<a href="javascript:alert(1)">클릭</a>', "클릭")
        self.assert_html("https://example.com", "https://example.com")

    def test_text_is_escaped(self):
        self.assert_html("1 < 2 & 3", "1 &lt; 2 &amp; 3")
        self.assert_html("&lt;script&gt;", "&lt;script&gt;")

    def test_output_tags_always_pair(self):
        self.assert_html("<strong>a", "<strong>a</strong>")
        self.assert_html("a</em>b", "ab")
        self.assert_html("<p><strong>a</p>b", "<p><strong>a</strong></p>b")

    def test_plain_text(self):
        self.assertEqual(format_content("<p>a</p><p>b</p>")[1], "a\nb")
        self.assertEqual(format_content("<ul><li>a</li><li>b<br>c</li></ul>")[1], "a\nb\nc")
        self.assertEqual(format_content("  1 < 2  ")[1], "1 < 2")
        self.assertEqual(format_content("<p></p>")[1], "")


class PostFormatApiTests(DebateBFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.join().status_code, 201)

    def test_saves_cleaned_html_and_plain_text(self):
        raw = '<p class="x"><b>굵게</b> 1 < 2</p><script>bad()</script><p>https://example.com</p>'
        response = self.write("formatted", raw)
        self.assertEqual(response.status_code, 201, response.content)

        post = Opinion.objects.get()
        self.assertEqual(post.content, "<p><b>굵게</b> 1 &lt; 2</p><p>https://example.com</p>")
        self.assertEqual(post.content_plain, "굵게 1 < 2\nhttps://example.com")
        [card] = self.client.get(self.posts_url).json()["data"]["posts"]
        self.assertEqual(card["content"], post.content)

    def test_tag_only_body_is_rejected(self):
        response = self.write("empty", "<p></p>")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], {"code": "VALIDATION_ERROR", "message": "내용을 입력해주세요"})
        self.assertFalse(Opinion.objects.exists())

    def test_banned_word_split_by_tags_is_caught(self):
        BannedWord.objects.create(word="금지어", level=BannedWord.Level.BLOCK)
        response = self.write("banned", "<b>금지</b>어")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "BANNED_WORD")

    def test_duplicate_check_uses_plain_text(self):
        self.assertEqual(self.write("first", "<b>같은 말</b>").status_code, 201)
        response = self.write("second", "<i>같은 말</i>")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["details"], {"reason": "DUPLICATE"})
