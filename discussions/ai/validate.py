"""LLM 결과 검사 (FC-DB-220-002 "없는 의견을 지어내지 않음").

항목 단위로 걸러내고, 판정 표현이 있으면 결과 전체를 버린다.
"""
import json
import re

from debates.models import Stance

MAX_ITEMS = 3
MAX_ITEM_CHARS = 200
JUDGE = re.compile(r"우세|압도|옳[다은]|타당하다|설득력이 (더|높)|이긴|승리")
# "~가 옳다는 의견이 있다"처럼 글쓴이의 주장을 옮긴 문장은 판정이 아니다(v2 검증 477건에서 확인).
ATTRIBUTED = re.compile(r"(는|라는|다는|이라는) (의견|주장|입장)이 있다|(다고|라고) 본다")
# "드러나지 않는 위험"처럼 내용 속 표현은 빈칸이 아니다. 의견·내용 자체가 없다는 문장만 거른다.
FILLER = re.compile(
    r"(의견|입장|주장|내용|근거)[이가은는]? ?(별도로 |명확히 |명확한 )?"
    r"(제시|언급|확인|나타나|드러나)(되지|하지|지) ?않"
    r"|내용이 없|의견이 없|찾을 수 없|해당 없|판단할 수 없"
)


class SummaryRejected(Exception):
    pass


def _bigrams(s):
    # 모든 항목에 붙는 "~는 의견이 있다"는 비교에서 뺀다. 남기면 다른 주장도 비슷해 보인다.
    s = ATTRIBUTED.sub("", s)
    s = re.sub(r"[\s.,!?]", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _near_dup(a, b):
    x, y = _bigrams(a), _bigrams(b)
    return bool(x and y) and len(x & y) / len(x | y) >= 0.7


def clean(raw, allowed_ids):
    """반환: {Stance.PRO: [{text, sourceIds}], Stance.CON: [...]}"""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise SummaryRejected("not json") from e
    if not isinstance(data, dict):
        raise SummaryRejected("not object")

    result = {}
    for key, stance in (("pro", Stance.PRO), ("con", Stance.CON)):
        kept = []
        for item in (data.get(key) or [])[:MAX_ITEMS]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            ids = [str(i) for i in (item.get("sourceIds") or [])]
            if not text or len(text) > MAX_ITEM_CHARS or FILLER.search(text):
                continue
            if JUDGE.search(text) and not ATTRIBUTED.search(text):
                raise SummaryRejected("judgement expression")
            ids = list(dict.fromkeys(i for i in ids if i in allowed_ids))
            if not ids:  # 근거가 없거나 입력에 없는 글만 가리키면 버린다
                continue
            if any(_near_dup(text, k["text"]) for k in kept):
                continue
            kept.append({"text": text, "sourceIds": ids})
        result[stance] = kept
    return result
