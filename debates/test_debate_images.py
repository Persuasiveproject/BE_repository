import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User

from .api.assets.checks import MAX_IMAGE_BYTES
from .models import Category, Debate, DebateImage
from .tasks.images import check_debate_image

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


class ImageFixture:
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        settings = override_settings(MEDIA_ROOT=media, MEDIA_URL="http://testserver/media/")
        settings.enable()
        self.addCleanup(settings.disable)

        self.creator = User.objects.create_user("creator@example.com", "Creator", coin_balance=5)
        self.other = User.objects.create_user("other@example.com", "Other")
        self.debate = Debate.objects.create(
            creator=self.creator, category=Category.objects.create(name="Society"),
            type=Debate.Type.QUICK, title="Draft", content="Topic", duration_minutes=10,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.creator)
        self.assets_url = reverse("debate-assets", args=[self.debate.pk])

    def body(self, **fields):
        return {"fileName": "photo.png", "contentType": "image/png", "fileSizeBytes": len(PNG), **fields}

    def reserve(self, **fields):
        return self.client.post(self.assets_url, self.body(**fields), format="json")

    def upload_url(self, response):
        parts = urlsplit(response.json()["data"]["uploadUrl"])
        return parts.path, parse_qs(parts.query)["token"][0]

    def upload(self, path, token, data=PNG):
        return APIClient().put(f"{path}?token={token}", data, content_type="application/octet-stream")

    def uploaded_image(self, data=PNG, **fields):
        response = self.reserve(**fields)
        self.assertEqual(self.upload(*self.upload_url(response), data=data).status_code, 204)
        return DebateImage.objects.get(pk=response.json()["data"]["assetId"])

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["error"]["code"], code)


class ReserveAssetTests(ImageFixture, TestCase):
    def test_reserves_upload_slot(self):
        response = self.reserve(fileName="../../etc/passwd")

        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        image = DebateImage.objects.get()
        self.assertEqual(data["assetId"], str(image.pk))
        self.assertIn("?token=", data["uploadUrl"])
        self.assertTrue(data["uploadUrl"].startswith("http://testserver/v1/assets/"))
        self.assertTrue(data["expiresAt"].endswith("Z"))
        self.assertEqual(
            (image.status, image.content_type, image.file_size_bytes, image.sort_order),
            (DebateImage.Status.PENDING_UPLOAD, "image/png", len(PNG), 0),
        )
        self.assertRegex(image.storage_key, rf"^debates/{self.debate.pk}/[0-9a-f]{{32}}\.png$")

    def test_invalid_body(self):
        self.assert_error(self.reserve(fileSizeBytes=MAX_IMAGE_BYTES + 1), 400, "FILE_TOO_LARGE")
        for fields in (
            {"contentType": "image/gif"}, {"fileSizeBytes": 0}, {"fileSizeBytes": "10"},
            {"fileSizeBytes": True}, {"fileName": ""}, {"contentType": "x" * 101},
        ):
            with self.subTest(fields):
                self.assert_error(self.reserve(**fields), 400, "VALIDATION_ERROR")
        self.assertFalse(DebateImage.objects.exists())

    def test_access_rules(self):
        self.assertEqual(APIClient().post(self.assets_url, self.body(), format="json").status_code, 401)
        self.client.force_authenticate(self.other)
        self.assert_error(self.reserve(), 403, "NOT_DEBATE_CREATOR")
        self.client.force_authenticate(self.creator)
        Debate.objects.filter(pk=self.debate.pk).update(status=Debate.Status.OPEN)
        self.assert_error(self.reserve(), 409, "NOT_DRAFT")
        Debate.objects.filter(pk=self.debate.pk).update(status=Debate.Status.CANCELED)
        self.assert_error(self.reserve(), 404, "DEBATE_NOT_FOUND")

    def test_limit_counts_active_images_and_reclaims_expired_reservations(self):
        for _ in range(3):
            self.assertEqual(self.reserve().status_code, 201)
        self.assert_error(self.reserve(), 409, "ASSET_LIMIT_EXCEEDED")

        first, second, third = DebateImage.objects.order_by("id")
        DebateImage.objects.filter(pk=first.pk).update(status=DebateImage.Status.REJECTED)
        self.assertEqual(self.reserve().status_code, 201)
        self.assert_error(self.reserve(), 409, "ASSET_LIMIT_EXCEEDED")

        DebateImage.objects.filter(pk=second.pk).update(created_at=timezone.now() - timedelta(minutes=16))
        self.assertEqual(self.reserve().status_code, 201)
        self.assertFalse(DebateImage.objects.filter(pk=second.pk).exists())
        self.assertEqual(list(DebateImage.objects.order_by("id").values_list("sort_order", flat=True)), [0, 2, 3, 4])


class UploadTests(ImageFixture, TestCase):
    def test_upload_stores_file(self):
        image = self.uploaded_image()

        self.assertEqual(image.status, DebateImage.Status.UPLOADED)
        self.assertIsNotNone(image.uploaded_at)
        with default_storage.open(image.storage_key) as file:
            self.assertEqual(file.read(), PNG)

    def test_bad_tokens_are_forbidden(self):
        path, token = self.upload_url(self.reserve())
        other_path, other_token = self.upload_url(self.reserve())
        for bad in ("broken", other_token):
            with self.subTest(bad=bad):
                self.assert_error(self.upload(path, bad), 403, "FORBIDDEN")
        with patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + 16 * 60):
            self.assert_error(self.upload(path, token), 403, "FORBIDDEN")
        self.assertFalse(DebateImage.objects.exclude(status=DebateImage.Status.PENDING_UPLOAD).exists())

    def test_second_upload_and_oversized_body_are_rejected(self):
        path, token = self.upload_url(self.reserve())
        self.assert_error(self.upload(path, token, b""), 400, "FILE_TOO_LARGE")
        self.assert_error(self.upload(path, token, b"x" * (MAX_IMAGE_BYTES + 1)), 400, "FILE_TOO_LARGE")
        self.assertEqual(self.upload(path, token).status_code, 204)
        self.assert_error(self.upload(path, token), 409, "ASSET_NOT_UPLOADABLE")


class CompleteTests(ImageFixture, TestCase):
    def complete(self, image, client=None):
        return (client or self.client).post(reverse("asset-complete", args=[image.pk]))

    def test_requires_uploaded_file(self):
        self.reserve()
        self.assert_error(self.complete(DebateImage.objects.get()), 409, "ASSET_NOT_UPLOADED")
        self.assert_error(self.client.post(reverse("asset-complete", args=[999999])), 404, "ASSET_NOT_FOUND")

    def test_other_member_is_not_owner(self):
        image = self.uploaded_image()
        self.client.force_authenticate(self.other)
        self.assert_error(self.complete(image), 403, "NOT_ASSET_OWNER")

    def test_starts_check_after_commit_and_repeats_202(self):
        image = self.uploaded_image()
        with patch("debates.api.assets.views.check_debate_image.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.complete(image)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json(), {"data": {"assetId": str(image.pk), "status": "CHECKING"}})
            self.assertEqual(len(callbacks), 1)
            delay.assert_called_once_with(image.pk)

            with self.captureOnCommitCallbacks(execute=True):
                again = self.complete(image)
            self.assertEqual((again.status_code, again.json()), (202, response.json()))
            self.assertEqual(delay.call_count, 2)
            delay.assert_called_with(image.pk)
            image.refresh_from_db()
            self.assertEqual(image.status, DebateImage.Status.CHECKING)

            DebateImage.objects.filter(pk=image.pk).update(status=DebateImage.Status.READY)
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                ready = self.complete(image)
            self.assertEqual((ready.status_code, callbacks), (202, []))
            self.assertEqual(delay.call_count, 2)


class CheckImageTaskTests(ImageFixture, TestCase):
    def checking(self, data=PNG, **fields):
        image = self.uploaded_image(data=data, **fields)
        DebateImage.objects.filter(pk=image.pk).update(status=DebateImage.Status.CHECKING)
        check_debate_image(image.pk)
        image.refresh_from_db()
        return image

    def test_real_png_becomes_ready(self):
        image = self.checking()

        self.assertEqual(image.status, DebateImage.Status.READY)
        self.assertEqual(image.image_url, f"http://testserver/media/{image.storage_key}")
        self.assertEqual((image.content_type, image.file_size_bytes), ("image/png", len(PNG)))
        self.assertIsNotNone(image.checked_at)

    def test_text_file_is_rejected(self):
        image = self.checking(b"just text")
        self.assertEqual((image.status, image.rejection_reason), (DebateImage.Status.REJECTED, "허용되지 않는 형식이에요"))
        self.assertIsNone(image.image_url)
        self.assertIsNotNone(image.checked_at)

    def test_declared_type_must_match(self):
        image = self.checking(JPEG)
        self.assertEqual((image.status, image.rejection_reason), (DebateImage.Status.REJECTED, "허용되지 않는 형식이에요"))

    def test_missing_file_is_rejected(self):
        image = self.uploaded_image()
        default_storage.delete(image.storage_key)
        DebateImage.objects.filter(pk=image.pk).update(status=DebateImage.Status.CHECKING)
        check_debate_image(image.pk)
        image.refresh_from_db()
        self.assertEqual(
            (image.status, image.rejection_reason), (DebateImage.Status.REJECTED, "업로드된 파일을 찾을 수 없어요"),
        )

    def test_ready_images_allow_publishing(self):
        self.checking()
        response = self.client.post(
            reverse("debate-publish", args=[self.debate.pk]), headers={"Idempotency-Key": "publish"},
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_unchecked_image_blocks_publishing(self):
        self.uploaded_image()
        response = self.client.post(
            reverse("debate-publish", args=[self.debate.pk]), headers={"Idempotency-Key": "publish"},
        )
        self.assert_error(response, 409, "ASSET_NOT_READY")
