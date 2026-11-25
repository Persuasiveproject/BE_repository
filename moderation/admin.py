from django.contrib import admin
from .models import BannedWord
from .models import Report

@admin.register(BannedWord)
class BannedWordAdmin(admin.ModelAdmin):
    list_display = ("word", "level", "created_at")
    list_filter = ("level",)
    search_fields = ("word",)



@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("target_type", "status", "reason", "created_at")
    list_filter = ("target_type", "status")
