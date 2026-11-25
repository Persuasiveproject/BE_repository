from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from .models import Stance, StanceHistory
from .stance import StanceConflict, change_stance
from .test_stance import StanceFixture


class StanceConcurrencyTests(StanceFixture, TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_two_requests_with_same_version_commit_one_change(self):
        ready = Barrier(2)

        def submit(stance):
            try:
                ready.wait(timeout=10)
                _, status = change_stance(
                    debate_id=self.debate.id, user=self.user, stance=stance,
                    opinion_id=self.opinion.id, version=0,
                    idempotency_key=f"device-{stance}",
                    request_path=reverse("my-stance", args=[self.debate.id]),
                )
                return status
            except StanceConflict as error:
                return error.error["code"]
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(submit, (Stance.CON, Stance.NEUTRAL)))

        self.assertCountEqual(outcomes, [200, "STANCE_CONFLICT"])
        self.participant.refresh_from_db()
        self.assertEqual(self.participant.version, 1)
        self.assertEqual(StanceHistory.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_retries_replay_the_same_direct_change(self):
        ready = Barrier(2)

        def submit():
            try:
                ready.wait(timeout=10)
                return change_stance(
                    debate_id=self.debate.id, user=self.user, stance=Stance.CON,
                    idempotency_key="same-key", request_path=reverse("my-stance", args=[self.debate.id]),
                )
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = [executor.submit(submit) for _ in range(2)]
            self.assertEqual(first.result(), second.result())

        self.assertEqual(StanceHistory.objects.count(), 1)
