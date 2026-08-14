from django.urls import path

from . import views

app_name = "ideas"

urlpatterns = [
    path("", views.home, name="home"),
    path("current/", views.current, name="current"),
    path("tracking/", views.tracking, name="tracking"),
    path("archive/", views.archive, name="archive"),
    path("graph/", views.graph, name="graph"),
    path("graph-lab/", views.graph_lab, name="graph_lab"),
    path("graph-lab/capability/", views.graph_lab_capability, name="graph_lab_capability"),
    path("graph-lab/export.graphml", views.graph_lab_export, name="graph_lab_export"),
    path("graph/relations/new/", views.graph_relation_create, name="graph_relation_create"),
    path("graph/relations/<int:pk>/delete/", views.graph_relation_delete, name="graph_relation_delete"),
    path("graph/suggestions/<int:pk>/<str:decision>/", views.graph_suggestion_review, name="graph_suggestion_review"),
    path("feeds/", views.feeds, name="feeds"),
    path("feeds/<int:pk>/rate/", views.rate_feed_item, name="rate_feed_item"),
    path("new/", views.idea_form, name="create"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/edit/", views.idea_form, name="edit"),
    path("<int:pk>/status/<str:status>/", views.set_status, name="set_status"),
    path("<int:pk>/next-action/", views.set_next_action, name="set_next_action"),
    path(
        "<int:pk>/next-actions/queue/",
        views.queue_next_action,
        name="queue_next_action",
    ),
    path(
        "<int:pk>/repeat-results/<int:result_pk>/",
        views.update_repeat_result,
        name="update_repeat_result",
    ),
    path("<int:pk>/repeat/pause/", views.toggle_repeat_pause, name="toggle_repeat_pause"),
    path(
        "<int:pk>/suggested-children/create/",
        views.create_suggested_child,
        name="create_suggested_child",
    ),
    path("<int:pk>/quick-update/", views.quick_update, name="quick_update"),
    path("<int:pk>/research/new/", views.add_research, name="add_research"),
    path(
        "<int:pk>/research/<int:entry_pk>/answers/",
        views.answer_research_questions,
        name="answer_research_questions",
    ),
    path("<int:pk>/continue-work/", views.continue_work, name="continue_work"),
    path("users/", views.user_management, name="user_management"),
]
