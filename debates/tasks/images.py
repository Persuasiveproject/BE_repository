from celery import shared_task
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from debates.api.assets.checks import ALLOWED_TYPES, MAX_IMAGE_BYTES, sniff_type
from debates.models import DebateImage


@shared_task(name="debates.tasks.check_debate_image")
@transaction.atomic
def check_debate_image(image_id):
    image = DebateImage.objects.select_for_update().filter(pk=image_id).first()
    if image is None or image.status != DebateImage.Status.CHECKING:
        return None
    image.checked_at = timezone.now()
    reason, size, detected = None, None, None
    if not image.storage_key or not default_storage.exists(image.storage_key):
        reason = "업로드된 파일을 찾을 수 없어요"
    else:
        size = default_storage.size(image.storage_key)
        with default_storage.open(image.storage_key, "rb") as file:
            detected = sniff_type(file.read(12))
        if not 0 < size <= MAX_IMAGE_BYTES:
            reason = "5MB 이하 이미지만 올릴 수 있어요"
        elif detected not in ALLOWED_TYPES or detected != image.content_type:
            reason = "허용되지 않는 형식이에요"

    if reason is None:
        image.status = DebateImage.Status.READY
        image.image_url = default_storage.url(image.storage_key)
        image.content_type = detected
        image.file_size_bytes = size
        fields = ["status", "image_url", "content_type", "file_size_bytes", "checked_at"]
    else:
        image.status = DebateImage.Status.REJECTED
        image.rejection_reason = reason
        fields = ["status", "rejection_reason", "checked_at"]
    image.save(update_fields=fields)
    return image.status
