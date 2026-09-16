from django.urls import path
from . import views

app_name = "evaluations"
urlpatterns = [
    path("interaction/", views.interaction, name="interaction"),
    path("research/<int:pk>/edit/", views.edit_research, name="edit_research"),
]
