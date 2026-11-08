from django.urls import include, path


urlpatterns = [
    path("", include("debates.api.participation.urls")),
    path("", include("debates.api.posts.urls")),
    path("", include("debates.api.joined.urls")),
    path("", include("debates.api.stance.urls")),
    path("", include("debates.api.timing.urls")),
    path("", include("debates.api.result.urls")),
]
