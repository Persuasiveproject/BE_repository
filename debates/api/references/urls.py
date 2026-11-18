from django.urls import path

from .views import PostReferenceView, ReferenceDetailView


urlpatterns = [
    path("posts/<int:post_id>/references", PostReferenceView.as_view(), name="post-references"),
    path("references/<int:reference_id>", ReferenceDetailView.as_view(), name="reference-detail"),
]
