from django.contrib import admin

from .models import Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("target_type", "status", "reason", "created_at")
    list_filter = ("target_type", "status")
