from django.conf import settings
from django.db import models


class CoinTransaction(models.Model):
    class Type(models.TextChoices):
        PURCHASE = "PURCHASE"
        AD_REWARD = "AD_REWARD"
        DEBATE_CREATE = "DEBATE_CREATE"
        REWARD = "REWARD"
        SIGNUP_BONUS = "SIGNUP_BONUS"
        ADMIN_GRANT = "ADMIN_GRANT"
        REFUND = "REFUND"

    class RefType(models.TextChoices):
        USER = "USER"
        DEBATE = "DEBATE"
        PAYMENT = "PAYMENT"
        AD_REWARD = "AD_REWARD"
        PERSUASION = "PERSUASION"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    amount = models.IntegerField()
    type = models.CharField(max_length=13, choices=Type.choices)
    balance_after = models.PositiveIntegerField()
    ref_type = models.CharField(max_length=10, choices=RefType.choices, null=True, blank=True)
    ref_id = models.PositiveBigIntegerField(null=True, blank=True)
    reverses_transaction = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="refund",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, db_column="created_by", on_delete=models.SET_NULL,
        related_name="issued_coin_transactions", null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "coin_transactions"


class CreditHistory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=18, decimal_places=6)
    reason = models.CharField(max_length=30)
    ref_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "credit_histories"


class Badge(models.Model):
    name = models.CharField(max_length=30)
    icon_url = models.URLField(max_length=500)
    required_credit = models.DecimalField(max_digits=18, decimal_places=6)
    description = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "badges"


class UserBadge(models.Model):
    pk = models.CompositePrimaryKey("user_id", "badge_id")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    badge = models.ForeignKey("economy.Badge", on_delete=models.CASCADE)
    acquired_at = models.DateTimeField(auto_now_add=True)
    is_representative = models.BooleanField(default=False)

    class Meta:
        db_table = "user_badges"


class Payment(models.Model):
    class ProductType(models.TextChoices):
        COIN_PACK = "COIN_PACK"
        SUBSCRIPTION = "SUBSCRIPTION"

    class Status(models.TextChoices):
        PENDING = "PENDING"
        PAID = "PAID"
        CANCELED = "CANCELED"
        FAILED = "FAILED"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    product_type = models.CharField(max_length=12, choices=ProductType.choices)
    product_id = models.CharField(max_length=50)
    amount = models.PositiveIntegerField()
    pg_provider = models.CharField(max_length=30)
    pg_tid = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payments"


class Subscription(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        EXPIRED = "EXPIRED"
        CANCELED = "CANCELED"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    plan = models.CharField(max_length=30)
    started_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    auto_renew = models.BooleanField(default=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "subscriptions"


class AdRewardLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    ad_network = models.CharField(max_length=30)
    reward_coin = models.PositiveIntegerField()
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ad_reward_logs"
