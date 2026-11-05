from django.urls import include, path


urlpatterns = [
    path("", include("debates.part_c.joined.urls")),
    path("", include("debates.part_c.stance.urls")),
    path("", include("debates.part_c.timing.urls")),
    path("", include("debates.part_c.result.urls")),
]
