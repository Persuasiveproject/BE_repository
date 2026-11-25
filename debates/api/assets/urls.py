from django.urls import path

from .views import AssetCompleteView, AssetUploadView, DebateAssetView


urlpatterns = [
    path("debates/<int:debate_id>/assets", DebateAssetView.as_view(), name="debate-assets"),
    path("assets/<int:asset_id>/upload", AssetUploadView.as_view(), name="asset-upload"),
    path("assets/<int:asset_id>/complete", AssetCompleteView.as_view(), name="asset-complete"),
]
