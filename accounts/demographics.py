from collections.abc import Mapping

from django.db import transaction
from django.utils import timezone

from debates.api_contract import ContractError
from debates.serializers import utc

from .models import User


REGIONS = {
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
    "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
    "경기": "경기도", "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
    "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도", "경남": "경상남도",
    "제주": "제주특별자치도",
}
REGION_NAMES = {**REGIONS, **{value: value for value in REGIONS.values()},
                "강원도": REGIONS["강원"], "전라북도": REGIONS["전북"], "제주도": REGIONS["제주"]}


def demographic_data(user):
    return {
        "gender": user.gender,
        "birthYear": user.birth_year,
        "region": user.region,
        "statisticsConsent": user.statistics_consent,
        "consentUpdatedAt": utc(user.statistics_consent_updated_at),
    }


def validate_demographics(data):
    invalid = ContractError(400, "DEMOGRAPHIC_INVALID", "성별, 출생 연도, 시·도와 통계 공개 동의를 확인해 주세요.")
    if not isinstance(data, Mapping) or data.keys() != {"gender", "birthYear", "region", "statisticsConsent"}:
        raise invalid
    gender, year, region, consent = (data[key] for key in ("gender", "birthYear", "region", "statisticsConsent"))
    if gender is not None and gender not in User.Gender.values:
        raise invalid
    if year is not None and (type(year) is not int or not 1900 <= year <= timezone.localdate().year):
        raise invalid
    if region is not None and (not isinstance(region, str) or region not in REGION_NAMES):
        raise invalid
    if type(consent) is not bool:
        raise invalid
    return gender, year, REGION_NAMES.get(region), consent


@transaction.atomic
def update_demographics(user_id, data=None):
    user = User.objects.select_for_update().get(pk=user_id)
    if user.status == User.Status.DELETED or (data is not None and user.status != User.Status.ACTIVE):
        raise ContractError(403, "FORBIDDEN", "이 계정은 정보를 변경할 수 없어요.")
    if data is None:
        if not user.statistics_consent:
            return user
        consent = False
        fields = []
    else:
        user.gender, user.birth_year, user.region, consent = data
        fields = ["gender", "birth_year", "region"]
    if user.statistics_consent != consent:
        user.statistics_consent = consent
        user.statistics_consent_updated_at = timezone.now()
        fields.extend(("statistics_consent", "statistics_consent_updated_at"))
    user.save(update_fields=[*fields, "updated_at"])
    return user
