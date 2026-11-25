from collections import Counter

from django.db.models import Case, CharField, Count, F, IntegerField, Q, When
from django.utils import timezone

from accounts.demographics import REGION_NAMES
from accounts.models import User

from .models import DebateParticipant


UNANSWERED = "미응답"


def participants_for_result(debate, *, closed_at=None):
    participants = DebateParticipant.objects.filter(debate=debate)
    cutoff = closed_at or debate.closed_at
    if cutoff is None:
        return participants.exclude(user__status=User.Status.DELETED)
    return participants.exclude(user__status=User.Status.DELETED, user__deleted_at__lte=cutoff)


def age_group(birth_year, year):
    age = year - birth_year if birth_year is not None else 0
    if age < 10:
        return UNANSWERED
    return "50대 이상" if age >= 50 else f"{age // 10 * 10}대"


def demographic_distributions(debate, *, minimum_participants):
    """Read consent at response time so previously published results reflect withdrawals."""
    can_share = Q(user__statistics_consent=True) & ~Q(user__status=User.Status.DELETED)
    rows = participants_for_result(debate).values(
        gender=Case(When(can_share, then=F("user__gender")), output_field=CharField()),
        birth_year=Case(When(can_share, then=F("user__birth_year")), output_field=IntegerField()),
        region=Case(When(can_share, then=F("user__region")), output_field=CharField()),
    ).annotate(count=Count("pk")).order_by()
    year = timezone.localtime(debate.closed_at or timezone.now()).year
    genders, ages, regions = Counter(), Counter(), Counter()
    for row in rows:
        count = row["count"]
        genders[row["gender"] if row["gender"] in User.Gender.values else UNANSWERED] += count
        ages[age_group(row["birth_year"], year)] += count
        regions[REGION_NAMES.get(row["region"], UNANSWERED)] += count
    total = sum(genders.values())
    if total < minimum_participants:
        return None, None, None

    def distribution(counts):
        return [
            {"label": label, "count": count, "percentage": round(count * 100 / total, 1)}
            for label, count in sorted(counts.items(), key=lambda item: (item[0] == UNANSWERED, item[0]))
        ]

    return distribution(genders), distribution(ages), distribution(regions)
