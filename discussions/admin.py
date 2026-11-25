from django.contrib import admin

from moderation.credit import moderate_opinion

from .models import Opinion


@admin.register(Opinion)
class OpinionAdmin(admin.ModelAdmin):
    list_display = ("id", "debate", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("=id",)
    list_select_related = ("debate",)
    actions = ("hide_and_reclaim",)
    readonly_fields = ("debate", "participant", "status", "content", "created_at")
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="글 숨김 및 영업 보상 회수", permissions=["change"])
    def hide_and_reclaim(self, request, queryset):
        for opinion_id in queryset.values_list("pk", flat=True).iterator():
            moderate_opinion(opinion_id)
        self.message_user(request, "선택한 글을 숨기고 해당 영업 보상을 회수했습니다.")
