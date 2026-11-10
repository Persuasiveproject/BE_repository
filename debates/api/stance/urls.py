from django.urls import path

from .views import MyStanceView, MyStanceEventListView


urlpatterns = [
    path("debates/<int:debate_id>/me/stance", MyStanceView.as_view(), name="my-stance"),
    path("debates/<int:debate_id>/me/events", MyStanceEventListView.as_view(), name="my-stance-events"),
]
