from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import exception_handler

from accounts.models import User


MAX_ID = 2**63 - 1


class ContractError(APIException):
    def __init__(self, status_code, code, message, *, details=None):
        self.status_code = status_code
        self.error = {"code": code, "message": message}
        if details is not None:
            self.error["details"] = details
        super().__init__(message, code=code)


def require_active_member(user):
    if user.status == User.Status.SUSPENDED:
        raise ContractError(403, "ACCOUNT_SUSPENDED", "이용이 제한된 계정이에요. 내 글 삭제와 신고만 할 수 있어요.")
    if user.status != User.Status.ACTIVE:
        raise ContractError(403, "FORBIDDEN", "You do not have permission.")


class IsActiveMember(IsAuthenticated):
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        require_active_member(request.user)
        return True


class ContractResponseMixin:
    def get_authenticate_header(self, request):
        for authenticator in self.get_authenticators():
            header = authenticator.authenticate_header(request)
            if header:
                return header
        return None

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if "debate_id" in kwargs and (
            not 1 <= kwargs["debate_id"] <= MAX_ID or len(request.path_info) > 255
        ):
            raise ContractError(400, "VALIDATION_ERROR", "Use a valid debate ID and request path.")

    def get_exception_handler(self):
        return contract_exception_handler


def contract_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, ContractError):
        response.data = {"error": exc.error}
        return response

    code, message = {
        400: ("VALIDATION_ERROR", "Check the request fields."),
        401: ("AUTHENTICATION_REQUIRED", "Sign in to continue."),
        403: ("FORBIDDEN", "You do not have permission."),
        404: ("NOT_FOUND", "The requested resource was not found."),
        405: ("METHOD_NOT_ALLOWED", "This method is not allowed."),
    }.get(response.status_code, ("REQUEST_FAILED", "The request failed."))
    details = response.data
    response.data = {"error": {"code": code, "message": message}}
    if isinstance(details, dict) and "detail" not in details:
        response.data["error"]["details"] = details
    return response
