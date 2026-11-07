from django.conf import settings
from django.db import models

from debates.models import Stance


class Opinion(models.Model):
    class Status(models.TextChoices):
        VISIBLE = "VISIBLE"
        FILTERED = "FILTERED"
        DELETED = "DELETED"

    debate = models.ForeignKey("debates.Debate", on_delete=models.CASCADE)
    participant = models.ForeignKey(
        "debates.DebateParticipant",
        on_delete=models.CASCADE,
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="replies",
        null=True,
        blank=True,
    )
    root = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="descendants",
        null=True,
        blank=True,
    )
    stance_at_write = models.CharField(max_length=7, choices=Stance.choices)
    content = models.TextField()
    content_plain = models.TextField()
    upvote_count = models.PositiveIntegerField(default=0)
    downvote_count = models.PositiveIntegerField(default=0)
    persuasion_count = models.PositiveIntegerField(default=0)
    reply_count = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=8,
        choices=Status.choices,
        default=Status.VISIBLE,
    )
    filter_reason = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "opinions"


class OpinionReference(models.Model):
    class SafetyStatus(models.TextChoices):
        SAFE = "SAFE"
        UNSAFE = "UNSAFE"
        PENDING = "PENDING"

    opinion = models.ForeignKey("discussions.Opinion", on_delete=models.CASCADE)
    url = models.URLField(max_length=2048)
    safe_browsing_status = models.CharField(
        max_length=7,
        choices=SafetyStatus.choices,
        default=SafetyStatus.PENDING,
    )
    checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "opinion_references"


class OpinionVote(models.Model):
    class VoteType(models.TextChoices):
        UP = "UP"
        DOWN = "DOWN"

    pk = models.CompositePrimaryKey("user_id", "opinion_id")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    opinion = models.ForeignKey("discussions.Opinion", on_delete=models.CASCADE)
    vote_type = models.CharField(max_length=4, choices=VoteType.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "opinion_votes"


class Persuasion(models.Model):
    opinion = models.ForeignKey("discussions.Opinion", on_delete=models.PROTECT)
    persuaded_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    from_stance = models.CharField(max_length=7, choices=Stance.choices)
    to_stance = models.CharField(max_length=7, choices=Stance.choices)
    reward_given = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "persuasions"


class AISummary(models.Model):
    class Side(models.TextChoices):
        PRO = Stance.PRO.value, Stance.PRO.label
        CON = Stance.CON.value, Stance.CON.label

    debate = models.ForeignKey("debates.Debate", on_delete=models.CASCADE)
    stance = models.CharField(max_length=7, choices=Side.choices)
    summary_text = models.TextField()
    source_opinion_ids = models.JSONField()
    model = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ai_summaries"
