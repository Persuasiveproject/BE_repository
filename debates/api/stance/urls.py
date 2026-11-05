from django.urls import path

from .views import MyStanceView


urlpatterns = [
    path("debates/<int:debate_id>/me/stance", MyStanceView.as_view(), name="my-stance"),
]
