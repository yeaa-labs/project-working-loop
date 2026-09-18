import json
import subprocess
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "check_invitation.py"


def run_check(payload):
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )


class CheckInvitationTests(unittest.TestCase):
    def test_exact_ascii_text_with_live_200_limit_passes(self):
        text = "Hello, Drew"
        result = run_check(
            {
                "draft_text": text,
                "visible_text": text,
                "live_limit": {
                    "basis": "counter",
                    "limit": 200,
                    "used": len(text),
                    "unit": "code_points",
                },
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["counts"]["code_points"], len(text))
        self.assertEqual(body["counts"]["utf16_units"], len(text))
        self.assertTrue(body["visible_text_matches_exactly"])
        self.assertTrue(body["within_live_limit"])
        self.assertEqual(body["issues"], [])

    def test_unicode_and_line_break_counts_are_reported(self):
        text = "A😀\nB"
        result = run_check(
            {
                "draft_text": text,
                "visible_text": text,
                "live_limit": {
                    "basis": "maxlength",
                    "limit": 10,
                    "unit": "utf16_units",
                },
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["counts"]["code_points"], 4)
        self.assertEqual(body["counts"]["utf16_units"], 5)
        self.assertEqual(body["effective_count"], 5)

    def test_visible_text_mismatch_is_a_contradiction(self):
        result = run_check(
            {
                "draft_text": "Line one\nLine two",
                "visible_text": "Line one Line two",
                "live_limit": {
                    "basis": "counter",
                    "limit": 300,
                    "used": 17,
                    "unit": "code_points",
                },
            }
        )
        self.assertEqual(result.returncode, 1)
        body = json.loads(result.stdout)
        self.assertIn("visible_text_mismatch", body["issues"])

    def test_overflow_uses_live_200_limit(self):
        text = "x" * 201
        result = run_check(
            {
                "draft_text": text,
                "visible_text": text,
                "live_limit": {
                    "basis": "counter",
                    "limit": 200,
                    "used": 201,
                    "unit": "code_points",
                },
            }
        )
        self.assertEqual(result.returncode, 1)
        body = json.loads(result.stdout)
        self.assertFalse(body["within_live_limit"])
        self.assertIn("live_limit_exceeded", body["issues"])

    def test_301_characters_pass_with_verified_live_500_limit(self):
        text = "x" * 301
        result = run_check(
            {
                "draft_text": text,
                "visible_text": text,
                "live_limit": {
                    "basis": "counter",
                    "limit": 500,
                    "used": 301,
                    "unit": "code_points",
                },
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["within_live_limit"])

    def test_unknown_live_limit_is_unverified(self):
        result = run_check(
            {"draft_text": "Hello", "visible_text": "Hello", "live_limit": None}
        )
        self.assertEqual(result.returncode, 1)
        body = json.loads(result.stdout)
        self.assertFalse(body["live_limit"]["verified"])
        self.assertIn("live_limit_basis_unknown", body["issues"])

    def test_unknown_counting_unit_without_observed_count_is_unverified(self):
        result = run_check(
            {
                "draft_text": "Hello",
                "visible_text": "Hello",
                "live_limit": {
                    "basis": "maxlength",
                    "limit": 420,
                    "unit": "unknown",
                },
            }
        )
        self.assertEqual(result.returncode, 1)
        body = json.loads(result.stdout)
        self.assertFalse(body["live_limit"]["verified"])
        self.assertIsNone(body["within_live_limit"])
        self.assertIn("live_limit_unit_unknown", body["issues"])

    def test_counter_contradiction_fails(self):
        result = run_check(
            {
                "draft_text": "Hello",
                "visible_text": "Hello",
                "live_limit": {
                    "basis": "counter",
                    "limit": 300,
                    "used": 6,
                    "unit": "code_points",
                },
            }
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("observed_count_contradiction", json.loads(result.stdout)["issues"])

    def test_invalid_input_uses_exit_2_without_echoing_private_text(self):
        private_text = "DO-NOT-ECHO-PRIVATE-MESSAGE"
        result = run_check({"draft_text": private_text, "visible_text": 42})
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(private_text, result.stdout + result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["error"], "invalid_input")


if __name__ == "__main__":
    unittest.main()
