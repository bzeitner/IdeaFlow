from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from ideas.graph.revision import mark_graph_changed
from ideas.models import Category, Idea, IdeaFeed, IdeaRelation, ResearchEntry, Resource, Stage


@receiver(post_save, sender=Idea)
@receiver(post_delete, sender=Idea)
@receiver(post_save, sender=IdeaRelation)
@receiver(post_delete, sender=IdeaRelation)
@receiver(post_save, sender=IdeaFeed)
@receiver(post_delete, sender=IdeaFeed)
@receiver(post_save, sender=Resource)
@receiver(post_delete, sender=Resource)
@receiver(post_save, sender=ResearchEntry)
@receiver(post_delete, sender=ResearchEntry)
@receiver(post_save, sender=Category)
@receiver(post_delete, sender=Category)
@receiver(post_save, sender=Stage)
@receiver(post_delete, sender=Stage)
def graph_source_changed(**kwargs):
    mark_graph_changed()
