from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Debate, DebateStatistics, Stance
from .results import save_debate_statistics, summarize_stances
from .tasks.closing import close_due_debates
from .tasks.results import compute_pending_results
from .result_state import clear_result_state, _redis, _key
from rest_framework.test import APIClient
from accounts.models import User
from discussions.models import Opinion
from .models import Category, DebateParticipant


class ResultFixture:
    def setUp(self):
        self.user = User.objects.create_user("member@example.com", "Member")
        self.other = User.objects.create_user("other@example.com", "Other")
        self.category = Category.objects.create(name="Society")
        self.debate = Debate.objects.create(
            creator=self.other, category=self.category, type=Debate.Type.QUICK,
            title="Debate", content="Topic", started_at=timezone.now(), status=Debate.Status.OPEN,
        )
        clear_result_state(self.debate.pk)
        self.addCleanup(clear_result_state, self.debate.pk)
        self.participant = self.join(self.user, 1, Stance.PRO)
        self.author = self.join(self.other, 2, Stance.CON)
        self.opinion = self.write_opinion(self.author)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def join(self, user, number, stance):
        return DebateParticipant.objects.create(
            debate=self.debate, user=user, anonymous_no=number,
            initial_stance=stance, current_stance=stance,
        )

    def write_opinion(self, participant, **kwargs):
        return Opinion.objects.create(
            debate=self.debate, participant=participant,
            stance_at_write=participant.current_stance, content="Reason",
            content_plain="Reason", **kwargs,
        )


class SummarizeStancesTests(SimpleTestCase):
    def test_counts_unchanged_and_changed_by_final_stance(self):
        result = summarize_stances([
            {"initial_stance": Stance.PRO, "current_stance": Stance.PRO, "count": 2},
            {"initial_stance": Stance.PRO, "current_stance": Stance.CON, "count": 1},
        ])
        self.assertEqual(result["total_participants"], 3)
        self.assertEqual(result["final_pro"], 2)
        self.assertEqual(result["final_con"], 1)
        self.assertEqual(result["final_neutral"], 0)
        self.assertEqual(result["stance_kept_count"], 2)
        self.assertEqual(result["stance_changed_count"], 1)


class DebateFlowTests(ResultFixture, TestCase):
    def test_change_close_aggregate_and_read_result(self):
        result_url = reverse("debate-result", args=[self.debate.id])
        self.assertEqual(self.client.get(result_url).status_code, 409)
        self.write_opinion(self.participant, parent=self.opinion, root=self.opinion)
        self.participant.current_stance = Stance.CON
        self.participant.save(update_fields=["current_stance"])
        self.debate.ends_at = timezone.now()
        self.debate.save(update_fields=["ends_at"])

        self.assertEqual(close_due_debates(), 1)
        pending = self.client.get(result_url)
        self.assertEqual(pending.status_code, 409)
        self.assertEqual(pending.data["error"]["code"], "RESULT_NOT_READY")
        self.assertEqual(pending.data["error"]["details"]["status"], "PENDING")

        self.assertEqual(compute_pending_results(), 1)
        self.assertEqual(compute_pending_results(), 0)
        self.assertEqual(DebateStatistics.objects.count(), 1)
        self.assertEqual(DebateStatistics.objects.get().stance_changed_count, 1)

        self.client.force_authenticate(user=None)
        result = self.client.get(result_url)
        self.assertEqual(result.status_code, 200)
        data = result.data["data"]
        self.assertEqual(data["counts"], {
            "totalParticipants": 2, "opinionCount": 1, "rebuttalCount": 1,
            "finalPro": 0, "finalCon": 2, "finalNeutral": 0,
            "stanceChangedCount": None, "stanceKeptCount": None,
        })
        self.assertEqual(data["sankeyData"], [])
        self.assertTrue(data["computedAt"].endswith("Z"))

    def test_draft_and_canceled_debates_have_no_public_result(self):
        url = reverse("debate-result", args=[self.debate.id])
        self.client.force_authenticate(user=None)
        self.debate.status = Debate.Status.DRAFT
        self.debate.save(update_fields=["status"])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.debate.status = Debate.Status.CANCELED
        self.debate.save(update_fields=["status"])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "RESULT_NOT_READY")
        self.assertEqual(compute_pending_results(), 0)

    def test_failure_rolls_back_one_result_and_retries_without_blocking_others(self):
        self.debate.status = Debate.Status.CLOSED
        self.debate.save(update_fields=["status"])
        other_debate = Debate.objects.create(
            creator=self.other, category=self.category, type=Debate.Type.ENDLESS,
            title="Second debate", content="Topic", status=Debate.Status.CLOSED,
        )
        url = reverse("debate-result", args=[self.debate.id])

        def fail_after_insert(debate):
            save_debate_statistics(debate)
            if debate.id == self.debate.id:
                raise RuntimeError("Simulated result failure")

        with patch("debates.results.save_debate_statistics", side_effect=fail_after_insert) as save:
            with self.assertLogs("debates.results", level="ERROR"):
                self.assertEqual(compute_pending_results(), 1)
            self.assertEqual(compute_pending_results(), 0)
            self.assertEqual(save.call_count, 2)

        self.assertFalse(DebateStatistics.objects.filter(debate=self.debate).exists())
        self.assertTrue(DebateStatistics.objects.filter(debate=other_debate).exists())
        failed = self.client.get(url)
        self.assertEqual(failed.status_code, 409)
        self.assertEqual(failed.data["error"]["code"], "RESULT_NOT_READY")
        self.assertEqual(failed.data["error"]["details"]["status"], "FAILED")

        # Losing the ephemeral failure hint must not strand the durable work.
        clear_result_state(self.debate.pk)
        self.assertEqual(compute_pending_results(), 1)
        self.assertIsNone(_redis().get(_key(self.debate.pk)))
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_sankey_visibility_changes_at_twenty_participants(self):
        self.debate.status = Debate.Status.CLOSED
        self.debate.save(update_fields=["status"])
        self.assertEqual(compute_pending_results(), 1)
        url = reverse("debate-result", args=[self.debate.id])
        result = DebateStatistics.objects.filter(debate=self.debate)

        result.update(total_participants=19)
        small = self.client.get(url).data["data"]
        self.assertEqual(small["sankeyData"], [])
        self.assertIsNone(small["counts"]["stanceChangedCount"])
        self.assertIsNone(small["counts"]["stanceKeptCount"])
        result.update(total_participants=20)
        large = self.client.get(url).data["data"]
        self.assertEqual(large["sankeyData"], result.get().sankey_data)
        self.assertEqual(large["counts"]["stanceChangedCount"], result.get().stance_changed_count)
        self.assertEqual(large["counts"]["stanceKeptCount"], result.get().stance_kept_count)
