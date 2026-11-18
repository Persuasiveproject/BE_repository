from django.contrib import admin

from .models import BannedWord


@admin.register(BannedWord)
class BannedWordAdmin(admin.ModelAdmin):
    list_display = ("word", "level", "created_at")
    list_filter = ("level",)
    search_fields = ("word",)
