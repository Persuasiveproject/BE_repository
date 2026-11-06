from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models.functions import Coalesce
from django.utils import timezone


class Stance(models.TextChoices):
    PRO = "PRO", "찬성"
    CON = "CON", "반대"
    NEUTRAL = "NEUTRAL", "유보"


class Category(models.Model):
    name = models.CharField(max_length=30, unique=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "categories"


class DebateQuerySet(models.QuerySet):
    def with_deadline(self):
        # Older published rows can have no stored ends_at.
        return self.annotate(
            deadline=Coalesce(
                "ends_at",
                models.Case(
                    models.When(
                        type="ENDLESS",
                        then=models.F("started_at") + models.Value(timedelta(hours=72)),
                    ),
                    models.When(
                        type="QUICK",
                        duration_minutes__range=(5, 15),
                        then=models.F("started_at")
                        + models.ExpressionWrapper(
                            models.F("duration_minutes") * models.Value(timedelta(minutes=1)),
                            output_field=models.DurationField(),
                        ),
                    ),
                    output_field=models.DateTimeField(),
                ),
            ),
        )


class Debate(models.Model):
    class Type(models.TextChoices):
        ENDLESS = "ENDLESS"
        QUICK = "QUICK"

    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        OPEN = "OPEN"
        CLOSED = "CLOSED"
        CANCELED = "CANCELED"

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_debates",
    )
    category = models.ForeignKey("debates.Category", on_delete=models.PROTECT)
    type = models.CharField(max_length=7, choices=Type.choices)
    title = models.CharField(max_length=100)
    content = models.TextField()
    duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    coin_cost = models.PositiveIntegerField(default=0)
    participant_count = models.PositiveIntegerField(default=0)
    opinion_count = models.PositiveIntegerField(default=0)
    view_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)
    canceled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, db_column="canceled_by", on_delete=models.SET_NULL,
        related_name="canceled_debates", null=True, blank=True,
    )
    cancel_reason = models.CharField(max_length=255, null=True, blank=True)

    objects = DebateQuerySet.as_manager()

    def effective_status(self, at):
        # The caller loads the same deadline expression used by the closing task.
        if self.status == self.Status.OPEN and self.deadline and self.deadline <= at:
            return self.Status.CLOSED
        return self.status

    class Meta:
        db_table = "debates"


class DebateImage(models.Model):
    class Status(models.TextChoices):
        PENDING_UPLOAD = "PENDING_UPLOAD"
        UPLOADED = "UPLOADED"
        CHECKING = "CHECKING"
        READY = "READY"
        REJECTED = "REJECTED"

    debate = models.ForeignKey("debates.Debate", on_delete=models.CASCADE)
    image_url = models.URLField(max_length=500, null=True, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    storage_key = models.CharField(max_length=500, null=True, blank=True)
    status = models.CharField(
        max_length=14, choices=Status.choices, default=Status.PENDING_UPLOAD,
    )
    content_type = models.CharField(max_length=100, null=True, blank=True)
    file_size_bytes = models.PositiveIntegerField(null=True, blank=True)
    uploaded_at = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        db_table = "debate_images"


class DebateParticipant(models.Model):
    debate = models.ForeignKey("debates.Debate", on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    anonymous_no = models.PositiveIntegerField()
    initial_stance = models.CharField(max_length=7, choices=Stance.choices)
    current_stance = models.CharField(max_length=7, choices=Stance.choices)
    joined_at = models.DateTimeField(auto_now_add=True)
    last_read_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "debate_participants"
        constraints = [
            models.UniqueConstraint(
                fields=["debate", "user"],
                name="participant_debate_user_uniq",
            ),
            models.UniqueConstraint(
                fields=["debate", "anonymous_no"],
                name="participant_debate_anonymous_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-joined_at", "-id"], name="participant_user_joined_idx"),
        ]


class StanceHistory(models.Model):
    participant = models.ForeignKey(
        "debates.DebateParticipant",
        on_delete=models.CASCADE,
    )
    from_stance = models.CharField(max_length=7, choices=Stance.choices)
    to_stance = models.CharField(max_length=7, choices=Stance.choices)
    persuasion = models.ForeignKey(
        "discussions.Persuasion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "stance_histories"


class DebateStatistics(models.Model):
    debate = models.OneToOneField(
        "debates.Debate",
        on_delete=models.CASCADE,
        primary_key=True,
    )
    total_participants = models.PositiveIntegerField()
    opinion_count = models.PositiveIntegerField()
    rebuttal_count = models.PositiveIntegerField()
    final_pro = models.PositiveIntegerField()
    final_con = models.PositiveIntegerField()
    final_neutral = models.PositiveIntegerField()
    stance_changed_count = models.PositiveIntegerField()
    stance_kept_count = models.PositiveIntegerField()
    sankey_data = models.JSONField()
    gender_data = models.JSONField(null=True, blank=True)
    age_data = models.JSONField(null=True, blank=True)
    region_data = models.JSONField(null=True, blank=True)
    gender_hidden = models.BooleanField(default=False)
    age_hidden = models.BooleanField(default=False)
    computed_at = models.DateTimeField()

    class Meta:
        db_table = "debate_statistics"


class DebateParticipantSnapshot(models.Model):
    debate = models.ForeignKey("debates.Debate", on_delete=models.CASCADE)
    recorded_at = models.DateTimeField()
    total_count = models.PositiveIntegerField(default=0)
    pro_count = models.PositiveIntegerField(default=0)
    con_count = models.PositiveIntegerField(default=0)
    neutral_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "debate_participant_snapshots"
