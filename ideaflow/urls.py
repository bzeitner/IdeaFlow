from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("api/", include("ideas.api")),
    path("api/", include("ideas.podcast_api")),
    # PWA: served from the site root so the service worker's scope is "/".
    path(
        "manifest.json",
        TemplateView.as_view(
            template_name="ideas/pwa/manifest.json",
            content_type="application/manifest+json",
        ),
        name="manifest",
    ),
    path(
        "sw.js",
        TemplateView.as_view(
            template_name="ideas/pwa/sw.js",
            content_type="application/javascript",
        ),
        name="service_worker",
    ),
    path("", include("ideas.urls")),
]
