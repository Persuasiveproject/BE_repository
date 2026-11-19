from debates.api_contract import ContractError

from .models import BannedWord


def _normalize(text):
    return "".join(text.lower().split())


def require_no_banned_words(*texts):
    targets = [_normalize(text) for text in texts if text]
    if not targets:
        return
    words = BannedWord.objects.filter(level=BannedWord.Level.BLOCK).values_list("word", flat=True)
    # Skip words that are blank after normalizing; an empty pattern would match every text.
    patterns = [word for word in map(_normalize, words) if word]
    if any(pattern in target for pattern in patterns for target in targets):
        # Never reveal which word matched.
        raise ContractError(400, "BANNED_WORD", "금지 표현이 포함되어 있어요. 수정해주세요")
