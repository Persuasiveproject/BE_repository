from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from debates.api_contract import ContractError


MAX_URL_LENGTH = 2048
SHORTENER_HOSTS = frozenset({
    "bit.ly", "t.co", "goo.gl", "tinyurl.com", "is.gd", "ow.ly", "buff.ly", "rebrand.ly",
    "cutt.ly", "shorturl.at", "naver.me", "han.gl", "url.kr", "vo.la", "me2.do",
})
_https_url = URLValidator(schemes=["https"])


def _invalid(reason, message):
    return ContractError(400, "URL_INVALID", message, details={"reason": reason})


def check_reference_url(url):
    format_error = _invalid("FORMAT", "Enter a valid URL.")
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise format_error
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        raise format_error from None
    if not parts.scheme or not host:
        raise format_error
    if parts.scheme.lower() != "https":
        raise _invalid("HTTPS_REQUIRED", "Use an https:// URL.")
    try:
        _https_url(url)
    except ValidationError:
        raise format_error from None
    if host.removeprefix("www.") in SHORTENER_HOSTS:
        raise _invalid("SHORTENED_URL", "Enter the original URL instead of a shortened link.")
    return url
