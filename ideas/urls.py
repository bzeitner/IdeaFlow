from django.urls import path

from . import views

app_name = "ideas"

urlpatterns = [
    path("", views.home, name="home"),
    path("current/", views.current, name="current"),
    path("tracking/", views.tracking, name="tracking"),
    path("archive/", views.archive, name="archive"),
    path("feeds/", views.feeds, name="feeds"),
    path("feeds/<int:pk>/rate/", views.rate_feed_item, name="rate_feed_item"),
    path("new/", views.idea_form, name="create"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/edit/", views.idea_form, name="edit"),
    path("<int:pk>/status/<str:status>/", views.set_status, name="set_status"),
    path("<int:pk>/next-action/", views.set_next_action, name="set_next_action"),
    path("<int:pk>/quick-update/", views.quick_update, name="quick_update"),
    path("<int:pk>/research/new/", views.add_research, name="add_research"),
    path("<int:pk>/continue-work/", views.continue_work, name="continue_work"),
    path("users/", views.user_management, name="user_management"),
]
