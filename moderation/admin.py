from django.contrib import admin
from django.db import transaction
from django.utils import timezone

from .credit import moderate_opinion
from .models import BannedWord, Report


@admin.register(BannedWord)
class BannedWordAdmin(admin.ModelAdmin):
    list_display = ("word", "level", "created_at")
    list_filter = ("level",)
    search_fields = ("word",)


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("target_type", "status", "reason", "created_at")
    list_filter = ("target_type", "status")
    actions = ("hide_opinion", "accept_mild", "accept_moderate", "accept_severe", "accept_fraud", "reject_opinion")

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.opinion_id is not None:
            return ("status", "processed_at")
        return super().get_readonly_fields(request, obj)

    def _accept_opinions(self, request, queryset, severity=None):
        processed = 0
        ids = queryset.filter(status=Report.Status.PENDING, opinion__isnull=False).values_list("pk", flat=True)
        for report_id in ids.iterator():
            with transaction.atomic():
                report = Report.objects.select_for_update().filter(
                    pk=report_id, status=Report.Status.PENDING, opinion__isnull=False,
                ).first()
                if report is None:
                    continue
                moderate_opinion(report.opinion_id, severity=severity)
                report.status = Report.Status.ACCEPTED
                report.processed_at = timezone.now()
                report.save(update_fields=["status", "processed_at"])
                processed += 1
        self.message_user(request, f"의견 신고 {processed}건을 처리했습니다.")

    @admin.action(description="글 숨김 및 영업 보상 회수", permissions=["change"])
    def hide_opinion(self, request, queryset):
        self._accept_opinions(request, queryset)

    @admin.action(description="경미 위반 확정 및 감점", permissions=["change"])
    def accept_mild(self, request, queryset):
        self._accept_opinions(request, queryset, "mild")

    @admin.action(description="보통 위반 확정 및 감점", permissions=["change"])
    def accept_moderate(self, request, queryset):
        self._accept_opinions(request, queryset, "moderate")

    @admin.action(description="중대 위반 확정 및 감점", permissions=["change"])
    def accept_severe(self, request, queryset):
        self._accept_opinions(request, queryset, "severe")

    @admin.action(description="부정 계정 위반 확정 및 감점", permissions=["change"])
    def accept_fraud(self, request, queryset):
        self._accept_opinions(request, queryset, "fraud")

    @admin.action(description="의견 신고 기각", permissions=["change"])
    def reject_opinion(self, request, queryset):
        count = queryset.filter(status=Report.Status.PENDING, opinion__isnull=False).update(
            status=Report.Status.REJECTED, processed_at=timezone.now(),
        )
        self.message_user(request, f"의견 신고 {count}건을 기각했습니다.")
