from django.urls import path

from . import views

app_name = "ideas"

urlpatterns = [
    path("", views.current, name="current"),
    path("tracking/", views.tracking, name="tracking"),
    path("archive/", views.archive, name="archive"),
    path("new/", views.idea_form, name="create"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/edit/", views.idea_form, name="edit"),
    path("<int:pk>/status/<str:status>/", views.set_status, name="set_status"),
]
