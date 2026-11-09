from django.urls import include, path


urlpatterns = [
    path("", include("debates.api.joined.urls")),
    path("", include("debates.api.stance.urls")),
    path("", include("debates.api.timing.urls")),
    path("", include("debates.api.result.urls")),
    path("", include("debates.api.debates.urls")),
    path("", include("debates.api.publish.urls")),
    path("", include("debates.api.detail.urls")),
    path("", include("debates.api.category.urls")),
]
