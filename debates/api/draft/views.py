from debates.api_contract import ContractError
from debates.models import Category


def validated_draft(payload):
    if not payload.is_valid():
        raise ContractError(400, "DRAFT_INVALID", "Check the draft fields.", details=payload.errors)
    return payload.validated_data


def active_category(category_id):
    category = Category.objects.filter(pk=category_id, is_active=True).first()
    if category is None:
        raise ContractError(404, "CATEGORY_NOT_FOUND", "The category was not found.")
    return category
