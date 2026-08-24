from django.test import TestCase
from django.urls import reverse

from ideas.models import HelpMessage
from ideas.tests.helpers import make_user


class HelpConversationTests(TestCase):
    def setUp(self):
        self.user = make_user("member@example.com")
        self.admin = make_user("admin@example.com", roles=("role_admin",))

    def test_user_can_send_and_see_own_messages_and_admin_responses(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("ideas:help"), {"body": "I need help."})
        self.assertRedirects(response, reverse("ideas:help"))
        message = HelpMessage.objects.get()
        self.assertEqual(message.user, self.user)
        self.assertFalse(message.admin_response)

        HelpMessage.objects.create(user=self.user, sender=self.admin, body="We can help.", admin_response=True)
        response = self.client.get(reverse("ideas:help"))
        self.assertContains(response, "I need help.")
        self.assertContains(response, "We can help.")

    def test_user_cannot_see_another_users_conversation(self):
        other = make_user("other@example.com")
        HelpMessage.objects.create(user=other, sender=other, body="Private question")
        self.client.force_login(self.user)
        response = self.client.get(reverse("ideas:help"))
        self.assertNotContains(response, "Private question")

    def test_admin_sees_pending_banner_and_can_reply(self):
        HelpMessage.objects.create(user=self.user, sender=self.user, body="Please respond")
        HelpMessage.objects.create(user=self.user, sender=self.user, body="One more detail")
        self.client.force_login(self.admin)
        response = self.client.get(reverse("ideas:home"))
        self.assertContains(response, "1 help conversation")
        self.assertNotContains(response, "2 help conversations")

        response = self.client.post(
            reverse("ideas:help_admin_conversation", args=[self.user.pk]),
            {"body": "Admin response"},
        )
        self.assertRedirects(response, reverse("ideas:help_admin_conversation", args=[self.user.pk]))
        reply = HelpMessage.objects.latest("pk")
        self.assertTrue(reply.admin_response)

        response = self.client.get(reverse("ideas:home"))
        self.assertNotContains(response, "awaiting an admin response")

    def test_deleting_admin_preserves_reply_history(self):
        reply = HelpMessage.objects.create(
            user=self.user,
            sender=self.admin,
            body="A durable response",
            admin_response=True,
        )
        self.admin.delete()
        reply.refresh_from_db()
        self.assertIsNone(reply.sender)
        self.assertTrue(reply.admin_response)

        self.client.force_login(self.user)
        response = self.client.get(reverse("ideas:help"))
        self.assertContains(response, "A durable response")

    def test_long_conversation_opens_on_most_recent_page(self):
        HelpMessage.objects.bulk_create(
            [
                HelpMessage(
                    user=self.user,
                    sender=self.user,
                    body="Oldest only" if number == 0 else f"Message {number}",
                )
                for number in range(101)
            ]
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("ideas:help"))
        self.assertContains(response, "Message 100")
        self.assertNotContains(response, "Oldest only")
        self.assertContains(response, "← Older")

    def test_non_admin_cannot_open_admin_inbox(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("ideas:help_admin"))
        self.assertRedirects(response, reverse("ideas:home"))
