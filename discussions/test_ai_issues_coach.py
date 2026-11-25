import json
from typing import ClassVar
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from debates.models import Debate, Stance
from discussions.models import AIIssueMap, Opinion

from .ai import state
from .ai.coach import OK, SUGGEST, UNAVAILABLE, clean_coach
from .ai.issues import (
    MIN_ISSUE_POSTS,
    build_issue_text,
    clean_issues,
    generate_issue_map,
    select_issue_inputs,
)
from .ai.validate import SummaryRejected
from .test_ai_summary import SummaryFixture


def _clear_issue(debate_id):
    r = state._redis()
    for kind in ("lock", "fp", "fail"):
        r.delete(f"{state.PREFIX}:{kind}:issues:{debate_id}")


class IssueFixture(SummaryFixture):
    def setUp(self):
        super().setUp()
        _clear_issue(self.debate.pk)
        self.addCleanup(_clear_issue, self.debate.pk)

    def seed_many(self):
        self.seed()
        # MIN_ISSUE_POSTS 이상이 되도록 의견을 더한다.
        for n in range(MIN_ISSUE_POSTS):
            self.post(self.parts[n % 2], f"추가 의견 {n}")

    def issue_output(self, con=True):
        return json.dumps({"issues": [
            {"title": "학생 참여", "pro": [{"text": "축제가 학생을 모은다는 의견이 있다.", "sourceIds": [str(self.pro1.pk)]}],
             "con": [{"text": "시설 개선이 먼저라는 의견이 있다.", "sourceIds": [str(self.con1.pk)]}] if con else []},
            {"title": "비용 부담", "pro": [{"text": "후원금으로도 충당한다는 의견이 있다.", "sourceIds": [str(self.pro2.pk)]}],
             "con": []},
        ]}, ensure_ascii=False)


class IssueSelectionTests(IssueFixture, TestCase):
    def test_inputs_carry_writer_stance_for_replies(self):
        self.seed()
        items, stances = select_issue_inputs(self.debate.pk)
        self.assertEqual(stances[str(self.reply.pk)], Stance.CON)  # 찬성 의견에 단 반대 측 반박
        self.assertNotIn(str(self.neu.pk), stances)  # 유보 원 의견은 넣지 않는다
        text = build_issue_text(self.debate.title, items)
        self.assertIn(f"(id={self.reply.pk}, 반대)", text)


class CleanIssuesTests(SimpleTestCase):
    stances: ClassVar = {"1": Stance.PRO, "2": Stance.CON, "3": Stance.PRO}

    def raw(self, issues):
        return json.dumps({"issues": issues}, ensure_ascii=False)

    def test_drops_sources_from_the_other_side_and_keeps_one_sided_issue(self):
        out = clean_issues(self.raw([
            {"title": "비용", "pro": [{"text": "싸다는 의견이 있다.", "sourceIds": ["1", "2", "999"]}], "con": []},
            {"title": "안전", "pro": [], "con": [{"text": "위험하다는 의견이 있다.", "sourceIds": ["1"]}]},
        ]), self.stances)
        self.assertEqual(len(out), 1)  # 반대 주장에 찬성 글만 근거로 단 쟁점은 근거가 없어 빠진다
        self.assertEqual(out[0]["pro"]["sourceIds"], ["1"])
        self.assertIsNone(out[0]["con"])

    def test_rejects_judgement(self):
        with self.assertRaises(SummaryRejected):
            clean_issues(self.raw([
                {"title": "비용", "pro": [{"text": "찬성 측이 우세하다.", "sourceIds": ["1"]}], "con": []},
            ]), self.stances)

    def test_drops_long_titles_and_duplicate_titles(self):
        out = clean_issues(self.raw([
            {"title": "비용 부담", "pro": [{"text": "싸다는 의견이 있다.", "sourceIds": ["1"]}], "con": []},
            {"title": "비용 부담", "pro": [{"text": "후원이 있다는 의견이 있다.", "sourceIds": ["3"]}], "con": []},
            {"title": "가" * 40, "pro": [{"text": "길다는 의견이 있다.", "sourceIds": ["3"]}], "con": []},
        ]), self.stances)
        self.assertEqual([i["title"] for i in out], ["비용 부담"])


@override_settings(AI_SUMMARY_MONTHLY_BUDGET_USD=1000)
class GenerateIssueMapTests(IssueFixture, TestCase):
    def test_needs_enough_posts(self):
        self.seed()
        self.assertEqual(generate_issue_map(self.debate.pk), "insufficient")

    def test_creates_then_skips_until_inputs_change(self):
        self.seed_many()
        with patch("discussions.ai.issues.client.generate", return_value=(self.issue_output(), {})) as gen:
            self.assertEqual(generate_issue_map(self.debate.pk), "created")
            self.assertEqual(generate_issue_map(self.debate.pk), "unchanged")
            self.post(self.parts[1], "새 반대 의견")
            self.assertEqual(generate_issue_map(self.debate.pk), "created")
        self.assertEqual(gen.call_count, 2)
        self.assertEqual(AIIssueMap.objects.filter(debate=self.debate).count(), 2)

    def test_quick_debates_are_skipped(self):
        Debate.objects.filter(pk=self.debate.pk).update(type=Debate.Type.QUICK)
        self.seed_many()
        self.assertEqual(generate_issue_map(self.debate.pk), "skipped")


class IssuesApiTests(IssueFixture, TestCase):
    def test_marks_awaiting_side_and_drops_hidden_sources(self):
        self.seed()
        AIIssueMap.objects.create(
            debate=self.debate, model="m",
            issues=clean_issues(self.issue_output(), select_issue_inputs(self.debate.pk)[1]),
        )
        Opinion.objects.filter(pk=self.con1.pk).update(status=Opinion.Status.DELETED)
        res = APIClient().get(reverse("debate-issues", args=[self.debate.pk]))
        self.assertEqual(res.status_code, 200)
        issues = res.json()["data"]["issues"]
        self.assertEqual(issues[0]["title"], "학생 참여")
        self.assertIsNone(issues[0]["con"])  # 근거 글이 지워져 반대 쪽이 내려갔다
        self.assertEqual(issues[0]["awaiting"], "CON")
        self.assertEqual(issues[1]["pro"]["sources"][0]["postId"], str(self.pro2.pk))

    def test_empty_when_no_map(self):
        res = APIClient().get(reverse("debate-issues", args=[self.debate.pk]))
        self.assertEqual(res.json()["data"], {"issues": [], "createdAt": None})


class CleanCoachTests(SimpleTestCase):
    draft = "찬성하는 사람들은 생각이 없는 것 같다. 등록금은 시설에 써야 한다."

    def raw(self, **kw):
        return json.dumps({"verdict": SUGGEST, "reasons": ["ATTACK"],
                           "suggestion": "등록금은 시설 개선에 먼저 쓰는 게 맞다고 생각한다.", **kw}, ensure_ascii=False)

    def test_keeps_valid_suggestion(self):
        out = clean_coach(self.raw(), self.draft)
        self.assertEqual(out["verdict"], SUGGEST)
        self.assertEqual(out["reasons"], ["ATTACK"])

    def test_falls_back_to_ok_for_unusable_suggestions(self):
        self.assertEqual(clean_coach(self.raw(suggestion=self.draft), self.draft)["verdict"], OK)
        self.assertEqual(clean_coach(self.raw(suggestion="반대 측이 옳다. " + "가" * 10), self.draft)["verdict"], OK)
        self.assertEqual(clean_coach(self.raw(suggestion="가" * 400), self.draft)["verdict"], OK)
        self.assertEqual(clean_coach(self.raw(reasons=["SPELLING"]), self.draft)["verdict"], OK)
        self.assertEqual(clean_coach("not json", self.draft)["verdict"], UNAVAILABLE)


class CoachApiTests(SummaryFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.users[1])
        self.url = reverse("debate-post-coach", args=[self.debate.pk])
        r = state._redis()
        for key in r.scan_iter(f"{state.PREFIX}:coach:{self.users[1].pk}:*"):
            r.delete(key)

    def test_unavailable_when_disabled(self):
        res = self.client.post(self.url, {"content": "찬성하는 사람들은 생각이 없다"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["data"]["verdict"], UNAVAILABLE)

    @override_settings(AI_COACH_ENABLED=True, GEMINI_API_KEY="test", AI_SUMMARY_MONTHLY_BUDGET_USD=1000)
    def test_suggests_and_passes_reply_target(self):
        self.seed()
        out = json.dumps({"verdict": SUGGEST, "reasons": ["ATTACK"], "suggestion": "후원금 비중이 작다고 본다."}, ensure_ascii=False)
        with patch("discussions.ai.coach.client.generate", return_value=(out, {})) as gen:
            res = self.client.post(self.url, {"content": "그걸 말이라고 하냐, 후원금은 작다", "parentId": str(self.pro2.pk)}, format="json")
        self.assertEqual(res.json()["data"], {"verdict": SUGGEST, "reasons": ["ATTACK"], "suggestion": "후원금 비중이 작다고 본다."})
        self.assertIn("[반박 대상] 후원금으로도 충당한다", gen.call_args.args[0])
        self.assertEqual(gen.call_args.kwargs["attempts"], 1)

    @override_settings(AI_COACH_ENABLED=True, GEMINI_API_KEY="test", AI_SUMMARY_MONTHLY_BUDGET_USD=1000)
    def test_llm_failure_never_blocks(self):
        with patch("discussions.ai.coach.client.generate", side_effect=TimeoutError("slow")):
            res = self.client.post(self.url, {"content": "찬성하는 사람들은 생각이 없다"}, format="json")
        self.assertEqual(res.json()["data"]["verdict"], UNAVAILABLE)

    def test_requires_participation(self):
        self.client.force_authenticate(user=self.users[0])
        DebateParticipantModel = self.parts[0].__class__
        DebateParticipantModel.objects.filter(pk=self.parts[0].pk).delete()
        res = self.client.post(self.url, {"content": "찬성하는 사람들은 생각이 없다"}, format="json")
        self.assertEqual(res.status_code, 403)
