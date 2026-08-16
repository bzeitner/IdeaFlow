from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PerIdeaAssessmentMigrationTests(TransactionTestCase):
    migrate_from = ("ideas", "0014_feeditem_content")
    migrate_to = ("ideas", "0015_per_idea_feed_item_assessments")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps

        Category = old_apps.get_model("ideas", "Category")
        Feed = old_apps.get_model("ideas", "Feed")
        FeedItem = old_apps.get_model("ideas", "FeedItem")
        Idea = old_apps.get_model("ideas", "Idea")
        IdeaFeed = old_apps.get_model("ideas", "IdeaFeed")

        category = Category.objects.create(name="Migration category", slug="migration")
        first = Idea.objects.create(title="First", category=category)
        second = Idea.objects.create(title="Second", category=category)

        sole_feed = Feed.objects.create(url="https://example.com/sole.xml")
        IdeaFeed.objects.create(idea=first, feed=sole_feed)
        self.sole_item_id = FeedItem.objects.create(
            feed=sole_feed, guid="sole", usefulness=4
        ).pk

        shared_feed = Feed.objects.create(url="https://example.com/shared.xml")
        IdeaFeed.objects.create(idea=first, feed=shared_feed)
        IdeaFeed.objects.create(idea=second, feed=shared_feed)
        self.shared_item_id = FeedItem.objects.create(
            feed=shared_feed, guid="shared", usefulness=5
        ).pk

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(
            MigrationExecutor(connection).loader.graph.leaf_nodes()
        )
        super().tearDown()

    def test_only_unambiguous_scores_are_migrated(self):
        Assessment = self.apps.get_model("ideas", "FeedItemAssessment")

        sole = Assessment.objects.get(item_id=self.sole_item_id)
        self.assertEqual(sole.usefulness, 4)
        self.assertFalse(
            Assessment.objects.filter(item_id=self.shared_item_id).exists()
        )


class IdeaOwnerMigrationTests(TransactionTestCase):
    migrate_from = ("ideas", "0026_update_build_execution_prompt")
    migrate_to = ("ideas", "0027_idea_created_by")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps

        User = old_apps.get_model("auth", "User")
        Category = old_apps.get_model("ideas", "Category")
        Idea = old_apps.get_model("ideas", "Idea")
        owner = User.objects.create(
            username="bzeitner", email="bzeitner@gmail.com"
        )
        category = Category.objects.create(name="Owned", slug="owned")
        self.owner_id = owner.pk
        self.idea_id = Idea.objects.create(title="Existing", category=category).pk

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.apps = executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(
            MigrationExecutor(connection).loader.graph.leaf_nodes()
        )
        super().tearDown()

    def test_existing_ideas_are_assigned_to_bzeitner(self):
        Idea = self.apps.get_model("ideas", "Idea")

        self.assertEqual(Idea.objects.get(pk=self.idea_id).created_by_id, self.owner_id)
