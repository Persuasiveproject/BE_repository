import json
from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from debates.models import Category, Debate, DebateParticipant, Stance
from discussions.models import AISummary, Opinion

from .ai import state
from .ai.prompt import build_user_text
from .ai.selection import fingerprint, input_ids, select_inputs
from .ai.service import generate_summary
from .ai.tasks import refresh_ai_summaries
from .ai.validate import SummaryRejected, clean


def _clear(debate_id):
    r = state._redis()
    for kind in ("lock", "fp", "fail", "final"):
        r.delete(f"{state.PREFIX}:{kind}:{debate_id}")


class SummaryFixture:
    def setUp(self):
        self.users = [User.objects.create_user(f"u{i}@example.com", f"U{i}") for i in range(4)]
        self.category = Category.objects.create(name="Society")
        self.debate = Debate.objects.create(
            creator=self.users[0], category=self.category, type=Debate.Type.ENDLESS,
            title="등록금으로 연예인을 섭외해야 하는가", content="Topic",
            started_at=timezone.now(), status=Debate.Status.OPEN,
        )
        _clear(self.debate.pk)
        self.addCleanup(_clear, self.debate.pk)
        stances = [Stance.PRO, Stance.CON, Stance.NEUTRAL, Stance.PRO]
        self.parts = [
            DebateParticipant.objects.create(
                debate=self.debate, user=u, anonymous_no=i + 1,
                initial_stance=s, current_stance=s,
            ) for i, (u, s) in enumerate(zip(self.users, stances))
        ]

    def post(self, part, text, upvotes=0, parent=None):
        return Opinion.objects.create(
            debate=self.debate, participant=part, stance_at_write=part.current_stance,
            content=text, content_plain=text, upvote_count=upvotes,
            parent=parent, root=parent,
        )

    def seed(self):
        self.pro1 = self.post(self.parts[0], "축제는 전교생이 모이는 행사다", upvotes=9)
        self.pro2 = self.post(self.parts[3], "후원금으로도 충당한다", upvotes=3)
        self.con1 = self.post(self.parts[1], "그 돈으로 냉난방기를 바꿀 수 있다", upvotes=7)
        self.reply = self.post(self.parts[1], "후원금 비중은 작다", upvotes=5, parent=self.pro2)
        self.neu = self.post(self.parts[2], "금액을 보고 정하겠다", upvotes=2)

    def llm_output(self, pro=None, con=None):
        return json.dumps({
            "pro": pro if pro is not None else [{"text": "축제가 학생을 모은다는 의견이 있다.", "sourceIds": [str(self.pro1.pk)]}],
            "con": con if con is not None else [{"text": "시설 개선이 먼저라는 의견이 있다.", "sourceIds": [str(self.con1.pk), str(self.reply.pk)]}],
        }, ensure_ascii=False)


class SelectionTests(SummaryFixture, TestCase):
    def test_selects_visible_top_opinions_by_stance_with_replies(self):
        self.seed()
        hidden = self.post(self.parts[0], "숨김 글", upvotes=99)
        hidden.status = Opinion.Status.DELETED
        hidden.save(update_fields=["status"])
        groups = select_inputs(self.debate.pk)
        self.assertEqual(list(groups), [Stance.PRO, Stance.CON])  # 유보는 [확인-6] 확정 전 제외
        self.assertEqual([i["id"] for i in groups[Stance.PRO]], [str(self.pro1.pk), str(self.pro2.pk)])
        self.assertEqual(groups[Stance.PRO][1]["replies"][0]["id"], str(self.reply.pk))
        self.assertNotIn(str(hidden.pk), input_ids(groups))
        text = build_user_text(self.debate.title, groups)
        self.assertIn(f"(id={self.con1.pk})", text)
        self.assertNotIn("99", text)  # 공감 수는 전달하지 않는다

    def test_fingerprint_changes_only_when_selection_changes(self):
        self.seed()
        before = fingerprint(select_inputs(self.debate.pk))
        Opinion.objects.filter(pk=self.pro1.pk).update(upvote_count=10)
        self.assertEqual(before, fingerprint(select_inputs(self.debate.pk)))
        self.post(self.parts[1], "새 반대 의견")
        self.assertNotEqual(before, fingerprint(select_inputs(self.debate.pk)))


class ValidateTests(SimpleTestCase):
    def test_drops_unknown_sources_filler_and_duplicates(self):
        raw = json.dumps({
            "pro": [
                {"text": "축제가 학생을 모은다는 의견이 있다.", "sourceIds": ["1"]},
                {"text": "축제가 학생을 모은다는 의견이 있다", "sourceIds": ["2"]},
                {"text": "지어낸 근거라는 의견이 있다.", "sourceIds": ["999"]},
            ],
            "con": [{"text": "반대 의견은 나타나지 않음.", "sourceIds": ["3"]}],
        }, ensure_ascii=False)
        out = clean(raw, {"1", "2", "3"})
        self.assertEqual(len(out[Stance.PRO]), 1)
        self.assertEqual(out[Stance.CON], [])

    def test_keeps_attributed_claims_and_content_negations(self):
        # v2 검증에서 나온 실제 문장: 글쓴이 주장을 옮긴 것과 내용 속 부정 표현은 남긴다.
        raw = json.dumps({
            "pro": [{"text": "임신 초기처럼 외관상 드러나지 않는 위험이 있을 수 있다는 의견이 있다.", "sourceIds": ["1"]}],
            "con": [{"text": "체벌은 없어지는 것이 옳다는 의견이 있다.", "sourceIds": ["2"]},
                    {"text": "명확한 중립 의견이나 보류 입장은 나타나지 않음.", "sourceIds": ["2"]}],
        }, ensure_ascii=False)
        out = clean(raw, {"1", "2"})
        self.assertEqual(len(out[Stance.PRO]), 1)
        self.assertEqual([i["text"] for i in out[Stance.CON]], ["체벌은 없어지는 것이 옳다는 의견이 있다."])

    def test_different_claims_with_same_ending_are_not_duplicates(self):
        raw = json.dumps({"pro": [
            {"text": "떡볶이에 튀김을 곁들여 먹자는 의견이 있다.", "sourceIds": ["1"]},
            {"text": "떡볶이에 어묵탕을 곁들여 먹자는 의견이 있다.", "sourceIds": ["2"]},
        ], "con": []}, ensure_ascii=False)
        self.assertEqual(len(clean(raw, {"1", "2"})[Stance.PRO]), 2)

    def test_rejects_judgement_and_bad_json(self):
        with self.assertRaises(SummaryRejected):
            clean(json.dumps({"pro": [{"text": "찬성이 우세하다.", "sourceIds": ["1"]}], "con": []}), {"1"})
        with self.assertRaises(SummaryRejected):
            clean("not json", {"1"})


@override_settings(AI_SUMMARY_ENABLED=True, GEMINI_API_KEY="test")
class GenerateTests(SummaryFixture, TestCase):
    def run_gen(self, output=None, **kwargs):
        with patch("discussions.ai.client.generate", return_value=(output or self.llm_output(), {
            "promptTokenCount": 500, "candidatesTokenCount": 100,
        })) as gen:
            result = generate_summary(self.debate.pk, **kwargs)
        return result, gen

    def test_needs_five_public_posts(self):
        self.post(self.parts[0], "하나")
        result, gen = self.run_gen("{}")
        self.assertEqual(result, "insufficient")
        gen.assert_not_called()

    def test_creates_pro_con_rows_and_skips_unchanged_selection(self):
        self.seed()
        result, _ = self.run_gen()
        self.assertEqual(result, "created")
        pro = AISummary.objects.get(stance=Stance.PRO)
        con = AISummary.objects.get(stance=Stance.CON)
        self.assertEqual(pro.summary_text, "- 축제가 학생을 모은다는 의견이 있다.")
        self.assertEqual(pro.source_opinion_ids, [self.pro1.pk])
        self.assertEqual(con.source_opinion_ids, sorted([self.con1.pk, self.reply.pk]))
        result, gen = self.run_gen()
        self.assertEqual(result, "unchanged")
        gen.assert_not_called()

    def test_deleted_source_drops_summary_and_regenerates(self):
        self.seed()
        self.run_gen()
        self.post(self.parts[2], "유보 의견 하나 더")  # 삭제 후에도 공개 글 5개 유지
        Opinion.objects.filter(pk=self.reply.pk).update(status=Opinion.Status.DELETED)
        out = self.llm_output(con=[{"text": "시설 개선이 먼저라는 의견이 있다.", "sourceIds": [str(self.con1.pk)]}])
        result, gen = self.run_gen(out)
        self.assertEqual(result, "created")
        gen.assert_called_once()
        for row in AISummary.objects.all():
            self.assertNotIn(self.reply.pk, row.source_opinion_ids)

    def test_deleted_source_drops_summary_even_without_regeneration(self):
        self.seed()
        self.run_gen()
        Opinion.objects.filter(pk=self.reply.pk).update(status=Opinion.Status.DELETED)
        result, gen = self.run_gen()
        self.assertEqual(result, "insufficient")  # 공개 글 4개
        gen.assert_not_called()
        self.assertFalse(AISummary.objects.filter(stance=Stance.CON).exists())
        self.assertTrue(AISummary.objects.filter(stance=Stance.PRO).exists())

    def test_llm_failure_backs_off_and_keeps_previous(self):
        self.seed()
        self.run_gen()
        self.post(self.parts[1], "새 반대 의견")
        with patch("discussions.ai.client.generate", side_effect=__import__(
                "discussions.ai.client", fromlist=["x"]).SummaryLLMError("boom")):
            with self.assertLogs("discussions.ai.service", level="WARNING"):
                self.assertEqual(generate_summary(self.debate.pk), "failed")
        self.assertEqual(AISummary.objects.count(), 2)
        self.assertEqual(self.run_gen()[0], "backoff")

    @override_settings(AI_SUMMARY_MONTHLY_BUDGET_USD=0)
    def test_budget_cap_stops_calls(self):
        self.seed()
        with self.assertLogs("discussions.ai.service", level="WARNING"):
            result, gen = self.run_gen()
        self.assertEqual(result, "budget")
        gen.assert_not_called()


@override_settings(AI_SUMMARY_ENABLED=True, GEMINI_API_KEY="test")
class TaskTests(SummaryFixture, TestCase):
    def test_disabled_by_default(self):
        with override_settings(AI_SUMMARY_ENABLED=False):
            self.assertEqual(refresh_ai_summaries(), 0)

    def test_interval_and_final_summary(self):
        self.seed()
        out = (self.llm_output(), {"promptTokenCount": 1, "candidatesTokenCount": 1})
        with patch("discussions.ai.client.generate", return_value=out) as gen:
            self.assertEqual(refresh_ai_summaries(), 1)
            self.post(self.parts[1], "새 반대 의견")
            self.assertEqual(refresh_ai_summaries(), 0)  # 5분이 안 지났다
            AISummary.objects.update(created_at=timezone.now() - timedelta(minutes=6))
            self.assertEqual(refresh_ai_summaries(), 1)
            self.assertEqual(gen.call_count, 2)

            # 종료 후 최종 요약 1회 (선정 의견이 같아도 만든다)
            self.debate.status = Debate.Status.CLOSED
            self.debate.closed_at = timezone.now()
            self.debate.save(update_fields=["status", "closed_at"])
            self.assertEqual(refresh_ai_summaries(), 1)
            self.assertEqual(refresh_ai_summaries(), 0)
            self.assertEqual(gen.call_count, 3)


class SummaryApiTests(SummaryFixture, TestCase):
    def test_returns_latest_per_stance_with_visible_sources_only(self):
        self.seed()
        AISummary.objects.create(debate=self.debate, stance=Stance.PRO, summary_text="old",
                                 source_opinion_ids=[self.pro1.pk], model="m")
        AISummary.objects.create(debate=self.debate, stance=Stance.PRO, summary_text="- new",
                                 source_opinion_ids=[self.pro1.pk, self.pro2.pk], model="m")
        Opinion.objects.filter(pk=self.pro2.pk).update(status=Opinion.Status.DELETED)
        res = self.client.get(f"/v1/debates/{self.debate.pk}/summary")
        self.assertEqual(res.status_code, 200)
        items = res.json()["data"]["summaries"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["stance"], "PRO")
        self.assertEqual(items[0]["summaryText"], "- new")
        self.assertEqual(items[0]["sourcePostIds"], [str(self.pro1.pk)])
        self.assertTrue(items[0]["createdAt"].endswith("Z"))

    def test_empty_and_not_found(self):
        res = self.client.get(f"/v1/debates/{self.debate.pk}/summary")
        self.assertEqual(res.json(), {"data": {"summaries": []}})
        self.debate.status = Debate.Status.DRAFT
        self.debate.save(update_fields=["status"])
        res = self.client.get(f"/v1/debates/{self.debate.pk}/summary")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["error"]["code"], "DEBATE_NOT_FOUND")
        self.assertEqual(self.client.get("/v1/debates/999999/summary").status_code, 404)
