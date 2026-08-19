from django.test import SimpleTestCase

from tools import review_relationships


class RelationshipCouncilJobTests(SimpleTestCase):
    def test_provider_plan_runs_both_claude_and_codex_across_three_personas(self):
        self.assertEqual(len(review_relationships.PROVIDERS), 3)
        self.assertEqual(set(review_relationships.PROVIDERS), {"claude", "codex"})

    def test_vote_parser_is_strict_and_accepts_valid_json(self):
        vote = review_relationships.parse_vote(
            '{"decision":"abstain","rationale":"Insufficient evidence."}'
        )
        self.assertEqual(vote["decision"], "abstain")

        with self.assertRaises(ValueError):
            review_relationships.parse_vote(
                '{"decision":"accept","rationale":""}'
            )

    def test_prompt_requires_independent_evidence_based_vote(self):
        prompt = review_relationships.prompt_for(
            {
                "suggestion_id": 1,
                "source": {"title": "A"},
                "target": {"title": "B"},
                "relationship": {"type": "supports", "evidence": "Measured result"},
                "personas": [],
            },
            {"name": "Risk", "goals": "Safety", "constraints": "No guessing"},
        )
        self.assertIn("independently", prompt.lower())
        self.assertIn("untrusted", prompt)
        self.assertIn("accept|reject|abstain", prompt)
