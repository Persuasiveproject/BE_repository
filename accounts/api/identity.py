"""HTTP adapter for the existing, explicitly enabled mock identity provider."""
from collections.abc import Mapping
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.identity import complete_mock_verification, start_mock_verification
from debates.api_contract import ContractError, ContractResponseMixin, IsActiveMember
from debates.serializers import utc


class CompleteIdentityInput(serializers.Serializer):
    requestId = serializers.CharField(min_length=1, max_length=128, trim_whitespace=False)
    proof = serializers.CharField(trim_whitespace=False)

    def to_internal_value(self, data):
        if not isinstance(data, Mapping) or set(data) != {"requestId", "proof"}:
            raise serializers.ValidationError({"non_field_errors": ["Use requestId and proof only."]})
        if not all(isinstance(data[key], str) for key in data):
            raise serializers.ValidationError({"non_field_errors": ["requestId and proof must be strings."]})
        return super().to_internal_value(data)


def provider_error():
    return ContractError(502, "IDENTITY_PROVIDER_ERROR", "본인 확인을 사용할 수 없어요.")


class IdentityStartView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def post(self, request):
        # The mock provider takes no personal data from the app.
        if not isinstance(request.data, Mapping) or request.data:
            raise ContractError(400, "VALIDATION_ERROR", "Use an empty object for the mock provider.")
        lifetime = settings.MOCK_IDENTITY_REQUEST_SECONDS
        if isinstance(lifetime, bool) or not isinstance(lifetime, int) or lifetime <= 0:
            raise provider_error()
        try:
            row = start_mock_verification(user_id=request.user.pk, valid_for=timedelta(seconds=lifetime))
        except ImproperlyConfigured:
            raise provider_error() from None
        return Response({"data": {"requestId": row.request_id, "expiresAt": utc(row.expires_at)}}, status=201)


class IdentityCompleteView(ContractResponseMixin, APIView):
    permission_classes = [IsActiveMember]

    def post(self, request):
        payload = CompleteIdentityInput(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            row = complete_mock_verification(
                user_id=request.user.pk, request_id=payload.validated_data["requestId"],
                proof=payload.validated_data["proof"],
            )
        except ImproperlyConfigured:
            raise provider_error() from None
        return Response({"data": {"identityVerified": True, "verifiedAt": utc(row.verified_at)}})
