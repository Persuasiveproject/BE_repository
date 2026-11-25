import json
import urllib.request
from datetime import timedelta

from django.conf import settings

REFERENCE_CHECK_TTL = timedelta(hours=24)
LOOKUP_URL = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
LOOKUP_LIMIT = 500
TIMEOUT_SECONDS = 10
THREAT_TYPES = ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"]


def lookup_unsafe(urls):
    """Return the subset of urls Safe Browsing flags. Network and HTTP errors propagate."""
    payload = {
        "client": {"clientId": "debate-backend", "clientVersion": "1.0"},
        "threatInfo": {
            "threatTypes": THREAT_TYPES,
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": url} for url in urls],
        },
    }
    request = urllib.request.Request(
        LOOKUP_URL, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": settings.SAFE_BROWSING_API_KEY},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        data = json.loads(response.read().decode())
    return {match["threat"]["url"] for match in data.get("matches", [])}
