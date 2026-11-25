from datetime import timedelta

from debates.api_contract import ContractError

MAX_IMAGES = 3
MAX_IMAGE_BYTES = 5 * 1024 * 1024
UPLOAD_TTL = timedelta(minutes=15)
# Content type -> storage key extension. The allowed list still needs team agreement.
ALLOWED_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def asset_input(data):
    invalid = ContractError(400, "VALIDATION_ERROR", "Check the asset fields.")
    if not isinstance(data, dict):
        raise invalid
    file_name, content_type, size = data.get("fileName"), data.get("contentType"), data.get("fileSizeBytes")
    if (
        not isinstance(file_name, str) or not file_name.strip() or len(file_name) > 255
        or not isinstance(content_type, str) or not 1 <= len(content_type) <= 100
        or isinstance(size, bool) or not isinstance(size, int) or size < 1
    ):
        raise invalid
    if size > MAX_IMAGE_BYTES:
        raise ContractError(400, "FILE_TOO_LARGE", "Images must be 5MB or smaller.")
    if content_type not in ALLOWED_TYPES:
        raise invalid
    return content_type, size


def sniff_type(head):
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None
