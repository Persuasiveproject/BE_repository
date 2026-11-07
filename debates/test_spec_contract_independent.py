"""Independent public HTTP checks derived only from the supplied original specs.

Baseline: fix/local-spec-alignment @ 7f898ce21560e9ae7592595b0cb8e98577817d0e.
API workbook: 영업중_API명세서_정합성반영본.xlsx (2026-10-14), 56 APIs.
Feature export: notion-feature-spec-2026-11-06.json, uniqueFeatures=189.

No application implementation, diff, commit contents, or existing tests were read.
Models are obtained through Django's public app registry by documented db_table;
only original API/DTO column names are supplied when seeding isolated test data.
Every behavior assertion uses APIClient HTTP responses. force_authenticate bypasses
token issuance/validation; this is not an end-to-end authentication integration test.
See independent-contract-expectations.md for unresolved policies and exact sources.
"""

from datetime import datetime, timedelta
from decimal import Decimal
import json
from uuid import uuid4

from django.apps import apps
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase


STANCES = {"PRO", "CON", "NEUTRAL"}

# Workbook 필드·DTO 정의!A52:J64: all 13 keys, not a selected projection.
DEBATE_LIST_ITEM_KEYS = {
    "id", "kind", "title", "status", "createdAt", "categoryId",
    "participantCount", "opinionCount", "viewCount", "startedAt", "endsAt",
    "closedAt", "canceledAt",
}
# Workbook 필드·DTO 정의!A65:J72: all eight keys, including nullable keys.
DEBATE_RESULT_KEYS = {
    "counts", "sankeyData", "genderData", "ageData", "regionData",
    "genderHidden", "ageHidden", "computedAt",
}
# Workbook 필드·DTO 정의!A121:J128: all eight keys.
RESULT_COUNTS_KEYS = {
    "totalParticipants", "opinionCount", "rebuttalCount", "finalPro", "finalCon",
    "finalNeutral", "stanceChangedCount", "stanceKeptCount",
}
# Workbook 필드·DTO 정의!A129:J133.
STANCE_EVENT_KEYS = {"id", "fromStance", "toStance", "persuasionId", "changedAt"}
# Workbook 상세 계약안!G31. version is not registered by this source.
STANCE_CHANGE_KEYS = {"currentStance", "eventId", "persuasionId", "reward"}


class IndependentSpecFixtures(APITestCase):
    """Fixture plumbing; never import app models, serializers, views, or helpers."""

    def setUp(self):
        self.http = APIClient()
        self.models_by_table = {m._meta.db_table: m for m in apps.get_models()}
        self.fixture_serial = 0
        self.member = self.make_user()
        self.other_member = self.make_user()
        self.outsider = self.make_user()
        self.category = self.seed("categories", name="독립 계약 검증", sort_order=1)
        self.debate = self.make_debate()
        self.participant = self.make_participant(self.debate, self.member, "PRO", 1)
        self.other_participant = self.make_participant(
            self.debate, self.other_member, "CON", 2
        )

    def seed(self, table, **columns):
        """Map documented SQL column names to ORM attnames through public metadata.

        An absent documented column is a fixture/schema error, never silently
        ignored. No unknown required column is guessed or automatically filled.
        """
        self.assertIn(table, self.models_by_table, "Documented table absent: " + table)
        model = self.models_by_table[table]
        attnames = {field.column: field.attname for field in model._meta.concrete_fields}
        missing = set(columns) - set(attnames)
        self.assertFalse(missing, f"Documented fixture columns absent in {table}: {missing}")
        return model._default_manager.create(
            **{attnames[column]: value for column, value in columns.items()}
        )

    def make_user(self):
        # API01 E/H; API06 UserProfile; common authentication rule ACTIVE.
        self.fixture_serial += 1
        token = uuid4().hex
        return self.seed(
            "users", provider="KAKAO", provider_id=token,
            email=f"independent-{token}@example.invalid", nickname=f"검증{self.fixture_serial}",
            status="ACTIVE", coin_balance=10, credit=Decimal("30.000000"),
        )

    def make_debate(self, status="OPEN", *, expired=False, participant_count=2):
        # API17 E/F, API22 E/H, and DebateListItem SQL mappings.
        self.fixture_serial += 1
        now = timezone.now()
        started = now - timedelta(hours=73 if expired or status == "CLOSED" else 1)
        ends = started + timedelta(hours=72)
        return self.seed(
            "debates", creator_id=self.member.pk, category_id=self.category.pk,
            type="ENDLESS", title=f"원본 명세 독립 검증 {self.fixture_serial}",
            content="공개 HTTP 계약 검증용 토론", duration_minutes=None, status=status,
            started_at=None if status == "DRAFT" else started,
            ends_at=None if status == "DRAFT" else ends,
            closed_at=now - timedelta(minutes=1) if status == "CLOSED" else None,
            participant_count=participant_count, opinion_count=0, view_count=0,
        )

    def make_participant(self, debate, member, stance="PRO", anonymous_no=1):
        # API25 E/H; Participation DTO; API28 E specifies joined_at.
        return self.seed(
            "debate_participants", debate_id=debate.pk, user_id=member.pk,
            anonymous_no=anonymous_no, initial_stance=stance, current_stance=stance,
            joined_at=timezone.now(),
        )

    def make_opinion(self, debate=None, participant=None, stance="CON", status="VISIBLE"):
        # API29 E, API30 H, OpinionItem DTO; no implementation-only field is supplied.
        debate = debate or self.debate
        participant = participant or self.other_participant
        return self.seed(
            "opinions", debate_id=debate.pk, participant_id=participant.pk,
            stance_at_write=stance, content="생각을 바꾼 의견", content_plain="생각을 바꾼 의견",
            status=status,
        )

    def make_event(self, participant, from_stance, to_stance, changed_at):
        # API27 H participant_id and all StanceEvent SQL mappings.
        return self.seed(
            "stance_histories", participant_id=participant.pk, from_stance=from_stance,
            to_stance=to_stance, persuasion_id=None, changed_at=changed_at,
        )

    def make_result(self, total=20, *, hidden=False, debate=None):
        debate = debate or self.make_debate("CLOSED", participant_count=total)
        # DebateResult + ResultCounts mappings only. JSON internals are intentionally
        # opaque: {} and [] are fixture values, not proposed production schemas.
        self.seed(
            "debate_statistics", debate_id=debate.pk, total_participants=total,
            opinion_count=2 if total else 0, rebuttal_count=1 if total else 0,
            final_pro=total, final_con=0,
            final_neutral=0, stance_changed_count=0, stance_kept_count=total,
            sankey_data=[], gender_data={}, age_data={}, region_data={},
            gender_hidden=hidden, age_hidden=hidden, computed_at=timezone.now(),
        )
        return debate

    def authenticate(self, member=None):
        self.http.force_authenticate(user=member or self.member)

    def anonymous(self):
        self.http.force_authenticate(user=None)

    @staticmethod
    def url(debate, suffix):
        return f"/v1/debates/{debate.pk}/{suffix}"

    def change(self, body, *, debate=None, key=None, include_key=True):
        headers = {"HTTP_IDEMPOTENCY_KEY": key if key is not None else uuid4().hex}
        return self.http.patch(
            self.url(debate or self.debate, "me/stance"), body, format="json",
            **(headers if include_key else {}),
        )

    def body(self, response, expected_status):
        self.assertEqual(response.status_code, expected_status, response.content.decode())
        self.assertTrue(response.headers.get("Content-Type", "").startswith("application/json"))
        value = response.json()
        self.assertIs(type(value), dict)
        return value

    def exact_keys(self, value, expected, label):
        self.assertIs(type(value), dict, label)
        actual = set(value)
        self.assertEqual(
            actual, set(expected),
            f"{label}: missing={sorted(set(expected)-actual)}; "
            f"unregistered={sorted(actual-set(expected))}",
        )

    def id_string(self, value):
        self.assertIs(type(value), str)
        self.assertRegex(value, r"^[0-9]+$")
        self.assertGreater(int(value), 0)

    def integer(self, value, *, minimum=None):
        self.assertIs(type(value), int)  # JSON booleans must not pass as integers.
        if minimum is not None:
            self.assertGreaterEqual(value, minimum)

    def utc_time(self, value):
        # ISO8601 UTC permits Z and +00:00; fractional seconds are not prescribed.
        self.assertIs(type(value), str)
        self.assertIn("T", value)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        self.assertIsNotNone(parsed.utcoffset())
        self.assertEqual(parsed.utcoffset(), timedelta(0))

    def json_value(self, value, *, nullable=False):
        if not nullable:
            self.assertIsNotNone(value)
        # JSON's internal object/array keys are expressly pending in the original.
        json.dumps(value, allow_nan=False)

    def error(self, response, status, code=None):
        value = self.body(response, status)
        self.exact_keys(value, {"error"}, "error envelope / common rules row 7")
        error = value["error"]
        self.assertIs(type(error), dict)
        self.assertTrue({"code", "message"}.issubset(error))
        self.assertFalse(set(error) - {"code", "message", "details"})
        self.assertIs(type(error["code"]), str)
        self.assertIs(type(error["message"]), str)
        if code is not None:
            self.assertEqual(error["code"], code)
        if "details" in error:
            self.assertIs(type(error["details"]), dict)
        return error

    def events(self, debate=None):
        return self.http.get(self.url(debate or self.debate, "me/events"))

    def event_schema(self, value):
        self.exact_keys(value, STANCE_EVENT_KEYS, "StanceEvent / DTO rows 129:133")
        self.id_string(value["id"])
        self.assertIn(value["fromStance"], STANCES)
        self.assertIn(value["toStance"], STANCES)
        if value["persuasionId"] is not None:
            self.id_string(value["persuasionId"])
        self.utc_time(value["changedAt"])

    def list_item_schema(self, value):
        self.exact_keys(value, DEBATE_LIST_ITEM_KEYS, "DebateListItem / DTO rows 52:64")
        self.id_string(value["id"])
        self.id_string(value["categoryId"])
        self.assertIn(value["kind"], {"ENDLESS", "QUICK"})
        self.assertIn(value["status"], {"DRAFT", "OPEN", "CLOSED", "CANCELED"})
        self.assertIs(type(value["title"]), str)
        self.assertLessEqual(len(value["title"]), 100)
        for field in ("participantCount", "opinionCount", "viewCount"):
            self.integer(value[field], minimum=0)
        self.utc_time(value["createdAt"])
        for field in ("startedAt", "endsAt", "closedAt", "canceledAt"):
            if value[field] is not None:
                self.utc_time(value[field])
        if value["status"] == "DRAFT":
            self.assertIsNone(value["startedAt"])
            self.assertIsNone(value["endsAt"])
        if value["status"] == "CLOSED":
            self.assertIsNotNone(value["closedAt"])
        if value["status"] == "CANCELED":
            self.assertIsNotNone(value["canceledAt"])

    def result_schema(self, response, total):
        value = self.body(response, 200)
        self.exact_keys(value, {"data"}, "API44 success envelope")
        data = value["data"]
        self.exact_keys(data, DEBATE_RESULT_KEYS, "DebateResult / DTO rows 65:72")
        counts = data["counts"]
        self.exact_keys(counts, RESULT_COUNTS_KEYS, "ResultCounts / DTO rows 121:128")
        self.assertEqual(counts["totalParticipants"], total)
        for field in RESULT_COUNTS_KEYS:
            if total < 20 and field in {"stanceChangedCount", "stanceKeptCount"}:
                # Explicit user agreement supersedes original non-null rows 127:128.
                self.assertIsNone(counts[field])
            else:
                self.integer(counts[field], minimum=0)
        self.assertIs(type(data["genderHidden"]), bool)
        self.assertIs(type(data["ageHidden"]), bool)
        self.json_value(data["sankeyData"])
        for field in ("genderData", "ageData", "regionData"):
            self.json_value(data[field], nullable=True)
        self.utc_time(data["computedAt"])
        if data["genderHidden"]:
            self.assertIsNone(data["genderData"])
        if data["ageHidden"]:
            self.assertIsNone(data["ageData"])
        return data


class SourceDerivedStrictResponseContractTests(IndependentSpecFixtures):
    """Whole-key comparisons expose unregistered extensions; no expectedFailure."""

    def test_api26_direct_change_has_exact_original_success_fields(self):
        self.authenticate()
        value = self.body(self.change({"stance": "CON"}), 200)
        self.exact_keys(value, {"data"}, "API26 success envelope")
        data = value["data"]
        self.exact_keys(data, STANCE_CHANGE_KEYS, "API26 / 상세 계약안 G31")
        self.assertIn(data["currentStance"], STANCES)
        self.assertEqual(data["currentStance"], "CON")
        self.id_string(data["eventId"])
        self.assertIsNone(data["persuasionId"])
        self.json_value(data["reward"], nullable=True)

    def test_api26_opinion_change_has_original_nullable_id_and_opaque_reward_types(self):
        opinion = self.make_opinion()
        self.authenticate()
        value = self.body(self.change({"stance": "CON", "opinionId": str(opinion.pk)}), 200)
        self.exact_keys(value, {"data"}, "API26 opinion success envelope")
        data = value["data"]
        self.exact_keys(data, STANCE_CHANGE_KEYS, "API26 opinion / 상세 계약안 G31")
        self.assertEqual(data["currentStance"], "CON")
        self.id_string(data["eventId"])
        self.id_string(data["persuasionId"])
        self.json_value(data["reward"], nullable=True)

    def test_api27_has_exact_envelope_array_and_all_event_fields(self):
        self.make_event(self.participant, "PRO", "NEUTRAL", timezone.now())
        self.authenticate()
        value = self.body(self.events(), 200)
        self.exact_keys(value, {"data"}, "API27 has no cursor/meta")
        self.exact_keys(value["data"], {"events"}, "API27 data")
        self.assertIs(type(value["data"]["events"]), list)
        self.assertEqual(len(value["data"]["events"]), 1)
        for event in value["data"]["events"]:
            self.event_schema(event)

    def test_api27_empty_events_is_array_and_has_no_pagination_fields(self):
        self.authenticate()
        value = self.body(self.events(), 200)
        self.exact_keys(value, {"data"}, "API27 empty envelope")
        self.exact_keys(value["data"], {"events"}, "API27 empty data")
        self.assertEqual(value["data"]["events"], [])

    def test_api28_has_exact_original_items_and_cursor_meta(self):
        self.authenticate()
        value = self.body(self.http.get("/v1/me/debates", {"status": "OPEN"}), 200)
        self.exact_keys(value, {"data", "meta"}, "API28 success envelope")
        self.exact_keys(value["data"], {"debates"}, "API28 data")
        self.exact_keys(value["meta"], {"nextCursor"}, "PagingMeta / DTO row 112")
        self.assertIs(type(value["data"]["debates"]), list)
        self.assertEqual(len(value["data"]["debates"]), 1)
        for item in value["data"]["debates"]:
            self.list_item_schema(item)
        cursor = value["meta"]["nextCursor"]
        if cursor is not None:
            self.assertIs(type(cursor), str)

    def test_api28_no_participation_is_empty_list_and_terminal_cursor_null(self):
        self.authenticate(self.outsider)
        value = self.body(self.http.get("/v1/me/debates", {"status": "OPEN"}), 200)
        self.exact_keys(value, {"data", "meta"}, "API28 empty envelope")
        self.exact_keys(value["data"], {"debates"}, "API28 empty data")
        self.exact_keys(value["meta"], {"nextCursor"}, "API28 empty meta")
        self.assertEqual(value["data"]["debates"], [])
        self.assertIsNone(value["meta"]["nextCursor"])

    def test_api44_anonymous_success_strictly_compares_original_8_by_8_keys(self):
        debate = self.make_result(total=20)
        self.anonymous()
        self.result_schema(self.http.get(self.url(debate, "result")), total=20)

    def test_api44_anonymous_19_keeps_all_keys_and_applies_agreed_null_override(self):
        debate = self.make_result(total=19, hidden=True)
        self.anonymous()
        self.result_schema(self.http.get(self.url(debate, "result")), total=19)

    def test_api44_anonymous_zero_keeps_visible_counts_and_agreed_null_keys(self):
        debate = self.make_result(total=0, hidden=True)
        self.anonymous()
        data = self.result_schema(self.http.get(self.url(debate, "result")), total=0)
        self.assertEqual(data["counts"]["opinionCount"], 0)
        self.assertEqual(data["counts"]["rebuttalCount"], 0)


class SourceDerivedInputPermissionAndErrorContractTests(IndependentSpecFixtures):
    def test_api26_27_28_require_authentication(self):
        self.anonymous()
        self.error(self.change({"stance": "CON"}), 401)
        self.error(self.events(), 401)
        self.error(self.http.get("/v1/me/debates"), 401)

    def test_api26_nonparticipant_has_exact_403_not_participant(self):
        self.authenticate(self.outsider)
        self.error(self.change({"stance": "CON"}), 403, "NOT_PARTICIPANT")

    def test_api27_nonparticipant_has_exact_403_not_participant(self):
        self.authenticate(self.outsider)
        self.error(self.events(), 403, "NOT_PARTICIPANT")

    def test_api26_closed_and_expired_open_have_exact_409_not_open(self):
        self.authenticate()
        for status, expired in (("CLOSED", False), ("OPEN", True)):
            with self.subTest(status=status, expired=expired):
                debate = self.make_debate(status, expired=expired)
                self.make_participant(debate, self.member)
                self.error(
                    self.change({"stance": "CON"}, debate=debate),
                    409, "DEBATE_NOT_OPEN",
                )

    def test_api26_wrong_debate_opinion_has_exact_400_mismatch(self):
        other_debate = self.make_debate()
        other_participant = self.make_participant(other_debate, self.other_member)
        opinion = self.make_opinion(other_debate, other_participant)
        self.authenticate()
        self.error(
            self.change({"stance": "CON", "opinionId": str(opinion.pk)}),
            400, "OPINION_DEBATE_MISMATCH",
        )

    def test_api26_idempotency_header_missing_empty_or_129_is_exact_invalid(self):
        self.authenticate()
        for label, key, include in (("missing", None, False), ("empty", "", True),
                                    ("129", "k" * 129, True)):
            with self.subTest(header=label):
                self.error(
                    self.change({"stance": "CON"}, key=key, include_key=include),
                    400, "IDEMPOTENCY_KEY_INVALID",
                )
        self.assertEqual(self.body(self.events(), 200)["data"]["events"], [])

    def test_api26_idempotency_1_and_128_character_keys_are_accepted(self):
        self.authenticate()
        for key, stance in (("k", "CON"), ("k" * 128, "NEUTRAL")):
            with self.subTest(length=len(key)):
                data = self.body(self.change({"stance": stance}, key=key), 200)["data"]
                self.assertEqual(data["currentStance"], stance)

    def test_api26_different_payload_same_scope_key_is_exact_409_reused(self):
        self.authenticate()
        key = uuid4().hex
        self.body(self.change({"stance": "CON"}, key=key), 200)
        self.error(self.change({"stance": "NEUTRAL"}, key=key), 409, "IDEMPOTENCY_KEY_REUSED")

    def test_api26_required_stance_rejects_missing_null_wrong_type_and_enum(self):
        self.authenticate()
        # Common rule specifies 400 input; no STANCE_* error name is invented.
        bodies = ({}, {"stance": None}, {"stance": ""}, {"stance": "pro"},
                  {"stance": "UNKNOWN"}, {"stance": 1}, {"stance": True},
                  {"stance": []}, {"stance": {}})
        for body in bodies:
            with self.subTest(body=body):
                self.error(self.change(body), 400)
        self.assertEqual(self.body(self.events(), 200)["data"]["events"], [])

    def test_api26_optional_opinion_id_rejects_null_number_and_nonpositive_strings(self):
        self.authenticate()
        for value in (None, 1, True, "", "0", "-1", "1.5", "abc", [], {}):
            with self.subTest(opinionId=value):
                self.error(self.change({"stance": "CON", "opinionId": value}), 400)
        self.assertEqual(self.body(self.events(), 200)["data"]["events"], [])

    def test_api26_missing_opinion_is_400_opinion_debate_mismatch(self):
        self.authenticate()
        # User decision 2026-11-06: missing opinion references use the API26
        # specific 400 error rather than the common missing-path-target 404.
        self.error(
            self.change({"stance": "CON", "opinionId": "9223372036854775807"}),
            400, "OPINION_DEBATE_MISMATCH",
        )

    def test_api28_status_invalid_values_are_exact_400_filter_invalid(self):
        self.authenticate()
        # CANCELED database enum is expressly not an automatically valid query enum.
        for value in ("", "DRAFT", "CANCELED", "open", "UNKNOWN"):
            with self.subTest(status=value):
                self.error(self.http.get("/v1/me/debates", {"status": value}), 400, "FILTER_INVALID")

    def test_api44_open_even_with_statistics_has_exact_409_result_not_ready(self):
        self.make_result(total=20, debate=self.debate)
        self.anonymous()
        self.error(self.http.get(self.url(self.debate, "result")), 409, "RESULT_NOT_READY")

    def test_api44_closed_without_statistics_has_exact_409_result_not_ready(self):
        debate = self.make_debate("CLOSED")
        self.anonymous()
        self.error(self.http.get(self.url(debate, "result")), 409, "RESULT_NOT_READY")

    def test_missing_debate_is_404_for_all_three_debate_scoped_apis(self):
        self.authenticate()
        prefix = "/v1/debates/9223372036854775807/"
        self.error(self.http.get(prefix + "me/events"), 404)
        self.error(self.http.get(prefix + "result"), 404)
        self.error(self.http.patch(prefix + "me/stance", {"stance": "CON"},
                                  format="json", HTTP_IDEMPOTENCY_KEY=uuid4().hex), 404)


class IndependentPublicHTTPFeatureBehaviorTests(IndependentSpecFixtures):
    """Functional evidence is deliberately separate from strict response contracts."""

    def test_direct_change_is_visible_in_own_events_with_null_persuasion(self):
        self.authenticate()
        changed = self.body(self.change({"stance": "CON"}), 200)["data"]
        events = self.body(self.events(), 200)["data"]["events"]
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["id"], changed["eventId"])
        self.assertEqual((event["fromStance"], event["toStance"]), ("PRO", "CON"))
        self.assertIsNone(event["persuasionId"])

    def test_all_three_stances_can_be_reached_by_actual_changes(self):
        self.authenticate()
        for stance in ("CON", "NEUTRAL", "PRO"):
            self.assertEqual(self.body(self.change({"stance": stance}), 200)["data"]["currentStance"], stance)
        events = self.body(self.events(), 200)["data"]["events"]
        self.assertEqual(len(events), 3)
        self.assertEqual({e["toStance"] for e in events}, STANCES)

    def test_same_payload_same_key_replays_original_response_and_one_event(self):
        self.authenticate()
        key = uuid4().hex
        first = self.change({"stance": "CON"}, key=key)
        first_body = self.body(first, 200)
        replay = self.change({"stance": "CON"}, key=key)
        self.assertEqual(self.body(replay, 200), first_body)
        self.assertEqual(len(self.body(self.events(), 200)["data"]["events"]), 1)

    def test_idempotency_scope_separates_members_and_actual_paths(self):
        key = uuid4().hex
        self.authenticate()
        first = self.body(self.change({"stance": "CON"}, key=key), 200)["data"]
        self.authenticate(self.other_member)
        second = self.body(self.change({"stance": "NEUTRAL"}, key=key), 200)["data"]
        self.assertNotEqual(first["eventId"], second["eventId"])
        other_debate = self.make_debate()
        self.make_participant(other_debate, self.other_member)
        third = self.body(self.change({"stance": "CON"}, debate=other_debate, key=key), 200)["data"]
        self.assertNotEqual(second["eventId"], third["eventId"])

    def test_events_are_owned_by_current_member_without_foreign_history_leakage(self):
        now = timezone.now()
        mine = self.make_event(self.participant, "PRO", "NEUTRAL", now)
        theirs = self.make_event(self.other_participant, "CON", "PRO", now)
        self.authenticate()
        mine_response = self.body(self.events(), 200)["data"]["events"]
        self.assertEqual([e["id"] for e in mine_response], [str(mine.pk)])
        self.authenticate(self.other_member)
        their_response = self.body(self.events(), 200)["data"]["events"]
        self.assertEqual([e["id"] for e in their_response], [str(theirs.pk)])

    def test_events_same_timestamp_are_complete_and_stably_ordered(self):
        now = timezone.now()
        events = [self.make_event(self.participant, a, b, now) for a, b in
                  (("PRO", "CON"), ("CON", "NEUTRAL"), ("NEUTRAL", "PRO"))]
        self.authenticate()
        first = self.body(self.events(), 200)["data"]["events"]
        second = self.body(self.events(), 200)["data"]["events"]
        self.assertEqual(first, second)
        self.assertEqual({e["id"] for e in first}, {str(e.pk) for e in events})
        # changed_at+id is specified; ascending/descending direction is not.
        ids = [int(e["id"]) for e in first]
        self.assertTrue(ids == sorted(ids) or ids == sorted(ids, reverse=True))

    def test_my_debates_status_filters_and_ownership_are_applied(self):
        closed = self.make_debate("CLOSED")
        self.make_participant(closed, self.member)
        not_mine = self.make_debate()
        self.make_participant(not_mine, self.other_member)
        self.authenticate()
        for status, expected_id in (("OPEN", self.debate.pk), ("CLOSED", closed.pk)):
            with self.subTest(status=status):
                items = self.body(self.http.get("/v1/me/debates", {"status": status}), 200)["data"]["debates"]
                self.assertEqual({item["id"] for item in items}, {str(expected_id)})
                self.assertTrue(all(item["status"] == status for item in items))

    def test_server_issued_cursor_can_be_followed_without_assuming_encoding_or_page_size(self):
        expected = {str(self.debate.pk)}
        for _ in range(24):
            debate = self.make_debate()
            self.make_participant(debate, self.member)
            expected.add(str(debate.pk))
        self.authenticate()
        seen, seen_cursors = set(), set()
        query = {"status": "OPEN"}
        for _ in range(30):
            page = self.body(self.http.get("/v1/me/debates", query), 200)
            ids = [item["id"] for item in page["data"]["debates"]]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertFalse(seen.intersection(ids), "Public cursor repeated an already seen debate")
            seen.update(ids)
            cursor = page["meta"]["nextCursor"]
            if cursor is None:
                break
            self.assertIs(type(cursor), str)
            self.assertNotIn(cursor, seen_cursors)
            seen_cursors.add(cursor)
            query = {"status": "OPEN", "cursor": cursor}
        else:
            self.fail("Server-issued cursors did not terminate for 25 seeded debates")
        self.assertEqual(seen, expected)
