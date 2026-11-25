from uuid import uuid4

from django.core import signing
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from debates.api_contract import MAX_ID, ContractError, ContractResponseMixin, IsActiveMember
from debates.models import Debate, DebateImage
from debates.serializers import utc
from debates.tasks.images import check_debate_image

from .checks import ALLOWED_TYPES, MAX_IMAGE_BYTES, MAX_IMAGES, UPLOAD_TTL, asset_input

UPLOAD_SALT = "asset-upload"


def require_valid_id(value, name):
    if not 1 <= value <= MAX_ID:
        raise ContractError(400, "VALIDATION_ERROR", f"Use a valid {name}.")


class DebateAssetView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    @transaction.atomic
    def post(self, request, debate_id):
        require_valid_id(debate_id, "debate ID")
        debate = Debate.objects.select_for_update().exclude(status=Debate.Status.CANCELED).filter(pk=debate_id).first()
        if debate is None:
            raise ContractError(404, "DEBATE_NOT_FOUND", "Debate not found.")
        if debate.creator_id != request.user.pk:
            raise ContractError(403, "NOT_DEBATE_CREATOR", "Only the creator can add images.")
        if debate.status != Debate.Status.DRAFT:
            raise ContractError(409, "NOT_DRAFT", "Images can only be added to drafts.")
        content_type, size = asset_input(request.data)

        now = timezone.now()
        images = DebateImage.objects.filter(debate=debate)
        # Expired upload reservations give their slot back.
        images.filter(status=DebateImage.Status.PENDING_UPLOAD, created_at__lt=now - UPLOAD_TTL).delete()
        if images.exclude(status=DebateImage.Status.REJECTED).count() >= MAX_IMAGES:
            raise ContractError(409, "ASSET_LIMIT_EXCEEDED", "A debate can have up to 3 images.")
        last_order = images.aggregate(last=Max("sort_order"))["last"]
        image = DebateImage.objects.create(
            debate=debate, status=DebateImage.Status.PENDING_UPLOAD, created_at=now,
            storage_key=f"debates/{debate.pk}/{uuid4().hex}{ALLOWED_TYPES[content_type]}",
            content_type=content_type, file_size_bytes=size,
            sort_order=0 if last_order is None else last_order + 1,
        )
        token = signing.dumps(image.pk, salt=UPLOAD_SALT)
        return Response({"data": {
            "assetId": str(image.pk),
            "uploadUrl": request.build_absolute_uri(reverse("asset-upload", args=[image.pk])) + "?token=" + token,
            "expiresAt": utc(image.created_at + UPLOAD_TTL),
        }}, status=201)


class AssetUploadView(ContractResponseMixin, APIView):
    # The signed token is the only credential, so session CSRF checks do not apply.
    authentication_classes = []
    permission_classes = [AllowAny]

    def put(self, request, asset_id):
        try:
            signed_id = signing.loads(
                request.query_params.get("token", ""), salt=UPLOAD_SALT, max_age=UPLOAD_TTL,
            )
        except signing.BadSignature:
            signed_id = None
        if signed_id != asset_id:
            raise ContractError(403, "FORBIDDEN", "Use a valid upload URL.")
        # Read the raw stream: request.body would hit DATA_UPLOAD_MAX_MEMORY_SIZE (2.5MB).
        body = request.read(MAX_IMAGE_BYTES + 1)
        if not body or len(body) > MAX_IMAGE_BYTES:
            raise ContractError(400, "FILE_TOO_LARGE", "Images must be 5MB or smaller.")

        with transaction.atomic():
            image = DebateImage.objects.select_for_update().filter(pk=asset_id).first()
            if image is None or image.status != DebateImage.Status.PENDING_UPLOAD:
                raise ContractError(409, "ASSET_NOT_UPLOADABLE", "This image cannot be uploaded again.")
            image.storage_key = default_storage.save(image.storage_key, ContentFile(body))
            image.status = DebateImage.Status.UPLOADED
            image.uploaded_at = timezone.now()
            image.save(update_fields=["storage_key", "status", "uploaded_at"])
        return Response(status=204)


class AssetCompleteView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    @transaction.atomic
    def post(self, request, asset_id):
        require_valid_id(asset_id, "asset ID")
        not_found = ContractError(404, "ASSET_NOT_FOUND", "Image not found.")
        debate_id = DebateImage.objects.filter(pk=asset_id).values_list("debate_id", flat=True).first()
        if debate_id is None:
            raise not_found
        # Lock order: debate -> image.
        debate = Debate.objects.select_for_update().filter(pk=debate_id).first()
        image = DebateImage.objects.select_for_update().filter(pk=asset_id, debate_id=debate_id).first()
        if debate is None or image is None:
            raise not_found
        if debate.creator_id != request.user.pk:
            raise ContractError(403, "NOT_ASSET_OWNER", "Only the creator can complete this upload.")
        if image.status not in (DebateImage.Status.UPLOADED, DebateImage.Status.CHECKING, DebateImage.Status.READY):
            raise ContractError(409, "ASSET_NOT_UPLOADED", "Upload the file first.")
        if image.status == DebateImage.Status.UPLOADED:
            image.status = DebateImage.Status.CHECKING
            image.save(update_fields=["status"])
        if image.status == DebateImage.Status.CHECKING:
            # Re-queue on retries too, in case the earlier task was lost.
            transaction.on_commit(lambda: check_debate_image.delay(image.pk))
        return Response({"data": {"assetId": str(image.pk), "status": DebateImage.Status.CHECKING}}, status=202)
