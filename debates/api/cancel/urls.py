from django.urls import path

from .views import DebateCancelView


urlpatterns = [
    path("admin/debates/<int:debate_id>/cancel", DebateCancelView.as_view(), name="debate-cancel"),
]
