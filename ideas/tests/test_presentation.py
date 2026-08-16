from django.test import SimpleTestCase

from ideas.presentation import render_research_context


class ResearchContextRenderingTests(SimpleTestCase):
    def test_links_references_to_known_research_entries(self):
        rendered = render_research_context(
            "Entry #12 confirmed it; research effort #9 disagreed.", {9, 12}
        )

        self.assertIn('href="#research-entry-12">Entry #12</a>', rendered)
        self.assertIn('href="#research-entry-9">research effort #9</a>', rendered)

    def test_does_not_link_unknown_entry_or_trust_report_html(self):
        rendered = render_research_context(
            '<script>alert(1)</script> Entry #99 and https://example.com', {12}
        )

        self.assertNotIn("<script>", rendered)
        self.assertNotIn('href="#research-entry-99"', rendered)
        self.assertIn('href="https://example.com"', rendered)
