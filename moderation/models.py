from django.conf import settings
from django.db import models


class Report(models.Model):
    class TargetType(models.TextChoices):
        OPINION = "OPINION"
        DEBATE = "DEBATE"
        REFERENCE = "REFERENCE"

    class Status(models.TextChoices):
        PENDING = "PENDING"
        ACCEPTED = "ACCEPTED"
        REJECTED = "REJECTED"

    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    target_type = models.CharField(
        max_length=9, choices=TargetType.choices, default=TargetType.OPINION,
    )
    opinion = models.ForeignKey(
        "discussions.Opinion", on_delete=models.PROTECT, null=True, blank=True,
    )
    debate = models.ForeignKey(
        "debates.Debate", on_delete=models.PROTECT, null=True, blank=True,
    )
    reference = models.ForeignKey(
        "discussions.OpinionReference", on_delete=models.PROTECT, null=True, blank=True,
    )
    reason = models.CharField(max_length=255)
    status = models.CharField(
        max_length=8,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "reports"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(target_type="OPINION", opinion__isnull=False, debate__isnull=True, reference__isnull=True)
                    | models.Q(target_type="DEBATE", opinion__isnull=True, debate__isnull=False, reference__isnull=True)
                    | models.Q(target_type="REFERENCE", opinion__isnull=True, debate__isnull=True, reference__isnull=False)
                ),
                name="report_target_matches_type",
            ),
        ]


class BannedWord(models.Model):
    class Level(models.TextChoices):
        BLOCK = "BLOCK"
        MASK = "MASK"

    word = models.CharField(max_length=50, unique=True)
    level = models.CharField(max_length=5, choices=Level.choices, default=Level.BLOCK)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "banned_words"
