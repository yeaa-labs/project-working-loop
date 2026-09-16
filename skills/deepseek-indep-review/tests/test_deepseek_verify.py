from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import socket
import stat
import tempfile
import urllib.error
import unittest
from unittest import mock
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "deepseek_verify.py"
SPEC = importlib.util.spec_from_file_location("deepseek_verify", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
deepseek_verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deepseek_verify)


def passing_bundle(round_number: int = 1) -> dict:
    return {
        "bundle_id": "B1",
        "verification_round": round_number,
        "original_request": "Create the approved deliverable.",
        "approved_plan_version": "V1",
        "requirements": [
            {
                "requirement_id": "R1",
                "text": "Create artifact A1.",
                "plan_step_ids": ["P1"],
            }
        ],
        "approved_plan": [{"plan_step_id": "P1", "text": "Create artifact A1."}],
        "artifacts": [{"artifact_id": "A1", "description": "Completed artifact."}],
        "evidence": [
            {
                "evidence_id": "E1",
                "description": "Read-back confirms artifact A1 exists.",
                "supports": ["R1", "P1", "A1"],
            }
        ],
        "previous_comments": [],
    }


def passing_verdict(round_number: int = 1) -> dict:
    return {
        "verification_round": round_number,
        "verdict": "verified_complete",
        "claim_complete_allowed": True,
        "summary": "All approved work is evidenced.",
        "checks": {
            "work_actually_performed": "pass",
            "approved_plan_completed": "pass",
            "requirements_aligned": "pass",
        },
        "coverage": [
            {
                "requirement_id": "R1",
                "plan_step_ids": ["P1"],
                "status": "complete",
                "expected": "Create artifact A1.",
                "observed": "Artifact A1 exists.",
                "evidence_refs": ["E1"],
            }
        ],
        "comments": [],
        "previous_comment_status": [],
    }


def rework_verdict(failure_type: str, round_number: int = 1) -> dict:
    verdict = passing_verdict(round_number)
    verdict["verdict"] = "rework_required"
    verdict["claim_complete_allowed"] = False
    verdict["checks"]["approved_plan_completed"] = "fail"
    verdict["coverage"][0]["status"] = "partial"
    verdict["comments"] = [
        {
            "comment_id": "C1",
            "failure_type": failure_type,
            "severity": "blocking",
            "requirement_id": "R1",
            "plan_step_ids": ["P1"],
            "what_was_expected": "Create artifact A1.",
            "what_was_observed": "The evidence does not prove completion.",
            "why_it_failed": "The approved result is incomplete or unproven.",
            "evidence_refs": ["E1"],
            "required_correction": "Complete the work and provide fresh evidence.",
        }
    ]
    return verdict


def completion_response(verdict: dict) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"content": json.dumps(verdict)}}]}
    ).encode("utf-8")


def wrap_error(inner):
    try:
        raise inner
    except BaseException as cause:
        outer = deepseek_verify.APIError(
            "DeepSeek did not return a usable response after three attempts"
        )
        outer.__cause__ = cause
        return outer


class SecretAndRedactionTests(unittest.TestCase):
    def test_environment_key_precedes_secret_file(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "deepseek.env"
            secret.write_text("DEEPSEEK_API_KEY=file-key\n", encoding="utf-8")
            secret.chmod(0o600)
            result = deepseek_verify.load_api_key(
                {"DEEPSEEK_API_KEY": "env-key"}, secret
            )
            self.assertEqual(result, "env-key")

    def test_secret_file_is_used_when_environment_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "deepseek.env"
            secret.write_text("DEEPSEEK_API_KEY=file-key\n", encoding="utf-8")
            secret.chmod(0o600)
            self.assertEqual(deepseek_verify.load_api_key({}, secret), "file-key")

    def test_missing_key_raises_configuration_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(deepseek_verify.ConfigurationError):
                deepseek_verify.load_api_key({}, Path(directory) / "missing.env")

    def test_secret_file_rejects_group_or_world_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "deepseek.env"
            secret.write_text("DEEPSEEK_API_KEY=file-key\n", encoding="utf-8")
            secret.chmod(0o644)
            with self.assertRaises(deepseek_verify.ConfigurationError):
                deepseek_verify.load_api_key({}, secret)

    def test_secret_file_rejects_duplicate_assignments(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "deepseek.env"
            secret.write_text(
                "DEEPSEEK_API_KEY=first\nDEEPSEEK_API_KEY=second\n",
                encoding="utf-8",
            )
            secret.chmod(0o600)
            with self.assertRaises(deepseek_verify.ConfigurationError):
                deepseek_verify.load_api_key({}, secret)

    def test_redaction_removes_nested_keys_and_token_patterns(self):
        source = {
            "api_key": "sk-supersecret123",
            "nested": {
                "log": "Authorization: Bearer abcdefghijklmnop",
                "safe": "E1 proves R1",
            },
        }
        redacted = deepseek_verify.redact_payload(source)
        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertNotIn("abcdefghijklmnop", redacted["nested"]["log"])
        self.assertEqual(redacted["nested"]["safe"], "E1 proves R1")


class BundleAndVerdictTests(unittest.TestCase):
    def test_passing_bundle_is_valid(self):
        deepseek_verify.validate_bundle(passing_bundle())

    def test_bundle_requires_round_between_one_and_three(self):
        bundle = passing_bundle(round_number=4)
        with self.assertRaises(deepseek_verify.VerificationError):
            deepseek_verify.validate_bundle(bundle)

    def test_bundle_requires_every_requirement_to_map_to_a_plan_step(self):
        bundle = passing_bundle()
        bundle["requirements"][0]["plan_step_ids"] = []
        with self.assertRaises(deepseek_verify.VerificationError):
            deepseek_verify.validate_bundle(bundle)

    def test_bundle_rejects_unknown_plan_step_mapping(self):
        bundle = passing_bundle()
        bundle["requirements"][0]["plan_step_ids"] = ["P404"]
        with self.assertRaises(deepseek_verify.VerificationError):
            deepseek_verify.validate_bundle(bundle)

    def test_verified_complete_requires_all_checks_to_pass(self):
        verdict = passing_verdict()
        verdict["checks"]["approved_plan_completed"] = "fail"
        with self.assertRaises(deepseek_verify.VerificationError):
            deepseek_verify.validate_verdict(verdict, passing_bundle())

    def test_verified_complete_requires_complete_coverage(self):
        verdict = passing_verdict()
        verdict["coverage"][0]["status"] = "partial"
        with self.assertRaises(deepseek_verify.VerificationError):
            deepseek_verify.validate_verdict(verdict, passing_bundle())

    def test_verified_complete_rejects_open_blocking_comments(self):
        verdict = passing_verdict()
        verdict["comments"] = [
            {
                "comment_id": "C1",
                "failure_type": "partially_completed",
                "severity": "blocking",
                "requirement_id": "R1",
                "plan_step_ids": ["P1"],
                "what_was_expected": "Expected",
                "what_was_observed": "Observed",
                "why_it_failed": "Failed",
                "evidence_refs": ["E1"],
                "required_correction": "Correct it",
            }
        ]
        with self.assertRaises(deepseek_verify.VerificationError):
            deepseek_verify.validate_verdict(verdict, passing_bundle())

    def test_non_pass_verdict_cannot_allow_completion_claim(self):
        verdict = rework_verdict("partially_completed")
        verdict["claim_complete_allowed"] = True
        with self.assertRaises(deepseek_verify.VerificationError):
            deepseek_verify.validate_verdict(verdict, passing_bundle())

    def test_successful_model_response_cannot_classify_work_as_verification_blocked(self):
        verdict = rework_verdict("insufficient_evidence")
        verdict["verdict"] = "verification_blocked"
        with self.assertRaises(deepseek_verify.VerificationError):
            deepseek_verify.validate_verdict(verdict, passing_bundle())

    def test_rework_accepts_all_target_failure_types(self):
        for failure_type in (
            "not_executed",
            "partially_completed",
            "requirement_drift",
            "insufficient_evidence",
        ):
            with self.subTest(failure_type=failure_type):
                deepseek_verify.validate_verdict(
                    rework_verdict(failure_type), passing_bundle()
                )

    def test_previous_comments_must_be_reconciled_after_round_one(self):
        bundle = passing_bundle(round_number=2)
        bundle["previous_comments"] = [{"comment_id": "C7", "summary": "Open"}]
        verdict = rework_verdict("partially_completed", round_number=2)
        verdict["previous_comment_status"] = []
        with self.assertRaises(deepseek_verify.VerificationError):
            deepseek_verify.validate_verdict(verdict, bundle)

    def test_prompt_contains_an_exact_scalar_status_and_comment_example(self):
        system_message = deepseek_verify.build_messages(passing_bundle())[0]["content"]
        self.assertIn('"work_actually_performed": "pass | fail | unverifiable"', system_message)
        self.assertIn('"status": "complete | partial | missing | mismatched | unverifiable"', system_message)
        self.assertIn('"what_was_expected"', system_message)
        self.assertIn('"required_correction"', system_message)
        self.assertIn("no artifacts and no evidence", system_message)
        self.assertIn("not_executed", system_message)


class BlockedVerdictTests(unittest.TestCase):
    def test_blocked_verdict_classifies_dns_failure(self):
        error = wrap_error(
            deepseek_verify.TransientAPIError("DeepSeek transport failed")
        )
        error.__cause__.__cause__ = urllib.error.URLError(
            socket.gaierror(8, "nodename nor servname provided")
        )
        blocker = deepseek_verify.blocked_verdict(passing_bundle(), error)["blocker"]
        self.assertEqual(blocker["category"], "dns_blocked")
        self.assertTrue(blocker["retryable"])

    def test_blocked_verdict_classifies_rate_limit(self):
        blocker = deepseek_verify.blocked_verdict(
            passing_bundle(),
            wrap_error(deepseek_verify.TransientAPIError(
                "DeepSeek returned transient HTTP 429"
            )),
        )["blocker"]
        self.assertEqual(blocker["category"], "http_429")

    def test_blocked_verdict_classifies_empty_and_invalid_json(self):
        empty = deepseek_verify.blocked_verdict(
            passing_bundle(),
            wrap_error(deepseek_verify.VerificationError(
                "DeepSeek returned empty audit content"
            )),
        )["blocker"]
        invalid = deepseek_verify.blocked_verdict(
            passing_bundle(),
            wrap_error(json.JSONDecodeError("bad", "x", 0)),
        )["blocker"]
        self.assertEqual(empty["category"], "empty_response")
        self.assertEqual(invalid["category"], "invalid_json")

    def test_cli_emits_safe_blocker_when_output_write_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle_path = Path(directory) / "bundle.json"
            output_path = Path(directory) / "verdict.json"
            bundle_path.write_text(json.dumps(passing_bundle()), encoding="utf-8")
            stderr = io.StringIO()
            stdout = io.StringIO()
            with (
                mock.patch.object(
                    deepseek_verify,
                    "load_api_key",
                    return_value="test-key",
                ),
                mock.patch.object(
                    deepseek_verify.DeepSeekClient,
                    "audit",
                    return_value=passing_verdict(),
                ),
                mock.patch.object(
                    deepseek_verify,
                    "write_json_atomic",
                    side_effect=OSError("raw output path should not leak"),
                ),
                contextlib.redirect_stderr(stderr),
                contextlib.redirect_stdout(stdout),
            ):
                result = deepseek_verify.main(
                    ["--bundle", str(bundle_path), "--output", str(output_path)]
                )
            self.assertEqual(result, 2)
            error_output = stderr.getvalue()
            self.assertNotIn("raw output path should not leak", error_output)
            payload = json.loads(error_output)
            self.assertEqual(payload["blocker"]["category"], "local_io_error")
            self.assertEqual(
                payload["blocker"]["detail"],
                "Local verifier I/O failed.",
            )

    def test_cli_handles_valid_json_non_object_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle_path = Path(directory) / "bundle.json"
            output_path = Path(directory) / "verdict.json"
            bundle_path.write_text("[1, 2, 3]", encoding="utf-8")
            stderr = io.StringIO()
            stdout = io.StringIO()
            with (
                contextlib.redirect_stderr(stderr),
                contextlib.redirect_stdout(stdout),
            ):
                result = deepseek_verify.main(
                    ["--bundle", str(bundle_path), "--output", str(output_path)]
                )
            self.assertEqual(result, 2)
            self.assertEqual(stdout.getvalue(), "")
            verdict_text = output_path.read_text(encoding="utf-8")
            error_output = stderr.getvalue()
            verdict = json.loads(verdict_text)
            error_payload = json.loads(error_output)
            self.assertEqual(verdict["verdict"], "verification_blocked")
            self.assertEqual(verdict["blocker"]["category"], "response_validation_failed")
            self.assertEqual(error_payload["blocker"], verdict["blocker"])
            self.assertNotIn("evidence bundle must be an object", verdict_text)
            self.assertNotIn("evidence bundle must be an object", error_output)


class APIClientTests(unittest.TestCase):
    def test_selects_pro_when_available(self):
        self.assertEqual(
            deepseek_verify.select_model(
                ["deepseek-v4-flash", "deepseek-v4-pro"], "deepseek-v4-pro"
            ),
            "deepseek-v4-pro",
        )

    def test_falls_back_to_flash_when_pro_is_absent(self):
        self.assertEqual(
            deepseek_verify.select_model(["deepseek-v4-flash"], "deepseek-v4-pro"),
            "deepseek-v4-flash",
        )

    def test_401_is_not_retried(self):
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append(url)
            return 401, b'{"error":{"message":"bad key"}}'

        client = deepseek_verify.DeepSeekClient(
            "key", transport=transport, sleep_fn=lambda _: None
        )
        with self.assertRaises(deepseek_verify.ConfigurationError):
            client.list_models()
        self.assertEqual(len(calls), 1)

    def test_429_is_retried_then_models_are_returned(self):
        responses = [
            (429, b'{"error":{"message":"rate limit"}}'),
            (
                200,
                b'{"data":[{"id":"deepseek-v4-pro"},{"id":"deepseek-v4-flash"}]}',
            ),
        ]

        def transport(method, url, headers, body, timeout):
            return responses.pop(0)

        client = deepseek_verify.DeepSeekClient(
            "key", transport=transport, sleep_fn=lambda _: None
        )
        self.assertEqual(
            client.list_models(), ["deepseek-v4-pro", "deepseek-v4-flash"]
        )
        self.assertEqual(responses, [])

    def test_empty_content_is_retried_then_audit_passes(self):
        verdict = passing_verdict()
        responses = [
            (
                200,
                b'{"data":[{"id":"deepseek-v4-pro"},{"id":"deepseek-v4-flash"}]}',
            ),
            (200, b'{"choices":[{"message":{"content":""}}]}'),
            (200, completion_response(verdict)),
        ]

        def transport(method, url, headers, body, timeout):
            return responses.pop(0)

        client = deepseek_verify.DeepSeekClient(
            "key", transport=transport, sleep_fn=lambda _: None
        )
        result = client.audit(passing_bundle())
        self.assertEqual(result["verdict"], "verified_complete")
        self.assertEqual(result["audit_metadata"]["model"], "deepseek-v4-pro")

    def test_invalid_json_exhausts_three_completion_attempts(self):
        responses = [
            (200, b'{"data":[{"id":"deepseek-v4-pro"}]}'),
            (200, b'{"choices":[{"message":{"content":"not json"}}]}'),
            (200, b'{"choices":[{"message":{"content":"still not json"}}]}'),
            (200, b'{"choices":[{"message":{"content":"again not json"}}]}'),
        ]

        def transport(method, url, headers, body, timeout):
            return responses.pop(0)

        client = deepseek_verify.DeepSeekClient(
            "key", transport=transport, sleep_fn=lambda _: None
        )
        with self.assertRaises(deepseek_verify.APIError):
            client.audit(passing_bundle())
        self.assertEqual(responses, [])


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_json_output_is_mode_600(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "verdict.json"
            deepseek_verify.write_json_atomic(output, passing_verdict())
            mode = stat.S_IMODE(output.stat().st_mode)
            self.assertEqual(mode, 0o600)
            self.assertEqual(json.loads(output.read_text())["verdict"], "verified_complete")


if __name__ == "__main__":
    unittest.main()
