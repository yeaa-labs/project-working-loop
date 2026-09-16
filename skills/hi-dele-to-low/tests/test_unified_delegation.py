"""Public-contract tests for the unified delegation controller.

The seams below are agreed by the approved implementation plan: configuration
resolution, qualitative route selection, staged dispatch, receipts, reviewer
gating, generated compatibility profiles, the restricted Claude heavy worker,
and the single configured capacity-exhaustion continuation.  None of these tests
starts a model process or uses a network, and every credential is a synthetic
temporary value.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from dataclasses import replace
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
CONFIG = SKILL / "config" / "delegation.toml"
sys.path.insert(0, str(SCRIPTS))

from delegation_core import (  # noqa: E402
    CLAUDE_ENVIRONMENT_KEYS,
    BoundaryError,
    ConfigError,
    ContinuationAttempt,
    ContinuationContext,
    DispatchRequest,
    RoutingInputError,
    acquire_direct_worker_lease,
    apply_reviewed_outputs,
    build_controller_command,
    build_reviewer_command,
    build_worker_command,
    classify_capacity_failure,
    classify_provider_balance_failure,
    continuation_attempt_origin,
    _dotenv_target_value,
    _reviewer_environment,
    effective_worker_receipt,
    evaluate_continuation_eligibility,
    generate_profile_text,
    load_config,
    plan_continuation_request,
    prepare_stage,
    receipt_digest,
    request_from_receipt,
    route_workload,
    run_controller_planner,
    run_independent_reviewer,
    run_staged_worker,
    run_staged_worker_with_continuation,
    validate_controller_receipt,
    validate_receipt,
)


COMPLETION_REPORT = {
    "status": "completed",
    "changed_outputs": ["generated/result.txt"],
    "self_tests": ["python -m unittest"],
    "limitations": [],
    "nested_dispatch": "not_attempted",
}


class UnifiedDelegationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG)

    def _request(self, root: Path, **overrides: object) -> DispatchRequest:
        root.mkdir(parents=True, exist_ok=True)
        source = root / "source"
        source.mkdir()
        (source / "notes.txt").write_text("approved input\n", encoding="utf-8")
        values: dict[str, object] = {
            "route": "deepseek",
            "source_root": source,
            "stage_root": root / "stage",
            "inputs": ("notes.txt",),
            "approved_outputs": ("generated/result.txt",),
            "brief": "Write only the declared staged output.",
            "timeout_seconds": 30,
            "workflow_selection": "declined",
            "controller_host": "codex",
        }
        values.update(overrides)
        return DispatchRequest(**values)

    def _test_environment(self) -> dict[str, str]:
        binding = self.config.resolve_worker_binding("deepseek")
        provider = self.config.providers[binding.provider or ""]
        return {
            "PATH": "/usr/bin",
            "HOME": "/synthetic/home",
            provider["environment_key"]: "test-only-token",
        }

    def _credential_config(
        self,
        root: Path,
        credential_file: Path,
    ) -> tuple[Path, DelegationConfig]:
        default_credential_file = self.config.providers[
            self.config.resolve_worker_binding("deepseek").provider or ""
        ]["credential_file"]
        config_path = root / "delegation.toml"
        config_path.write_text(
            CONFIG.read_text(encoding="utf-8").replace(
                f"credential_file = {json.dumps(str(default_credential_file))}",
                f"credential_file = {json.dumps(str(credential_file))}",
            ),
            encoding="utf-8",
        )
        return config_path, load_config(config_path)

    def _approved_controller_runner(
        self, command: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """A fresh-process test double for the required controller preflight."""

        workspace = Path(str(kwargs["cwd"]))
        manifest = json.loads(
            (workspace / "control" / "controller-request.json").read_text(encoding="utf-8")
        )
        outcome = {
            "status": "approved",
            "route": manifest["route"],
            "request_manifest": manifest,
            "plan_steps": ["implement the declared staged output"],
            "evidence": ["fresh configured controller assessed the proposed route"],
            "limitations": ["only staged inputs were available to the controller"],
            "fresh_process": True,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(outcome), "")

    def _counting_controller_runner(self, calls: list[str]):
        def controller_runner(
            command: tuple[str, ...], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            manifest = json.loads(
                (Path(str(kwargs["cwd"])) / "control" / "controller-request.json").read_text(
                    encoding="utf-8"
                )
            )
            calls.append(str(manifest["route"]))
            return self._approved_controller_runner(command, **kwargs)

        return controller_runner

    def _write_stage_outputs(self, stage_root: Path, files: dict[str, str]) -> None:
        outputs = Path(stage_root) / "outputs"
        for relative, text in files.items():
            path = outputs / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def _claude_envelope(
        self,
        *,
        result: str | None = None,
        is_error: bool = False,
        subtype: str = "success",
        model_usage: dict[str, object] | None = None,
        raw: dict[str, object] | None = None,
    ) -> str:
        if raw is not None:
            return json.dumps(raw)
        envelope: dict[str, object] = {
            "type": "result",
            "subtype": subtype,
            "is_error": is_error,
            "session_id": "synthetic-session",
        }
        if result is not None:
            envelope["result"] = result
        envelope["modelUsage"] = (
            {"claude-opus-5": {"inputTokens": 11, "outputTokens": 7}}
            if model_usage is None
            else model_usage
        )
        return json.dumps(envelope)

    def _claude_worker_runner(
        self,
        *,
        envelope: str,
        files: dict[str, str] | None = None,
        returncode: int = 0,
        captured: dict[str, object] | None = None,
        raises: BaseException | None = None,
    ):
        def runner(
            command: tuple[str, ...], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if captured is not None:
                captured["command"] = command
                captured["env"] = dict(kwargs["env"])  # type: ignore[arg-type]
                captured["cwd"] = str(kwargs["cwd"])
            # The Claude worker runs at the stage root itself.
            self._write_stage_outputs(Path(str(kwargs["cwd"])), files or {})
            if raises is not None:
                raise raises
            return subprocess.CompletedProcess(command, returncode, envelope, "")

        return runner

    def _codex_worker_runner(
        self,
        *,
        files: dict[str, str],
        report: dict[str, object],
        captured: dict[str, object] | None = None,
    ):
        def runner(
            command: tuple[str, ...], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if captured is not None:
                captured["command"] = command
                captured["env"] = dict(kwargs["env"])  # type: ignore[arg-type]
                captured["cwd"] = str(kwargs["cwd"])
            final_path = Path(command[command.index("--output-last-message") + 1])
            final_path.parent.mkdir(parents=True, exist_ok=True)
            # A Codex worker runs in the stage workspace.
            self._write_stage_outputs(Path(str(kwargs["cwd"])).parent, files)
            final_path.write_text(json.dumps(report), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        return runner

    def _codex_failure_runner(
        self,
        *,
        stdout: str,
        returncode: int = 1,
        files: dict[str, str] | None = None,
    ):
        """A Codex worker that fails without writing a completion report."""

        def runner(
            command: tuple[str, ...], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            self._write_stage_outputs(Path(str(kwargs["cwd"])).parent, files or {})
            return subprocess.CompletedProcess(command, returncode, stdout, "")

        return runner

    def _routed_runner(self, claude_runner, codex_runner):
        """Dispatch one synthetic runner per launcher without launching anything."""

        def runner(command: tuple[str, ...], **kwargs: object):
            if "--output-last-message" in command:
                return codex_runner(command, **kwargs)
            return claude_runner(command, **kwargs)

        return runner

    def _codex_provider_runner(self, deepseek_runner, terra_runner):
        """Split Codex worker runs by the child-only provider selection."""

        def runner(command: tuple[str, ...], **kwargs: object):
            if 'model_provider="deepseek"' in command:
                return deepseek_runner(command, **kwargs)
            return terra_runner(command, **kwargs)

        return runner

    def _deepseek_balance_envelope(
        self,
        *,
        message: str = "unexpected status 402 Payment Required: Insufficient Balance",
        url: str | None = None,
        envelope_type: str = "turn.failed",
    ) -> str:
        """The actual observed top-level Codex provider failure envelope shape."""

        provider = self.config.providers[
            self.config.resolve_worker_binding("deepseek").provider or ""
        ]
        endpoint = f"{provider['base_url']}/responses" if url is None else url
        return json.dumps(
            {
                "type": envelope_type,
                "error": {"message": message, "url": endpoint},
            }
        )

    def _exhausted_heavy_outcome(
        self,
        root: Path,
        *,
        envelope: str,
        controller_calls: list[str] | None = None,
        partial_text: str = "partial heavy progress\n",
        final_text: str = "continued result\n",
    ) -> tuple[DispatchRequest, dict[str, object]]:
        request = self._request(root, route="opus")
        calls = [] if controller_calls is None else controller_calls
        outcome = run_staged_worker_with_continuation(
            self.config,
            request,
            runner=self._routed_runner(
                self._claude_worker_runner(
                    envelope=envelope,
                    files={"generated/result.txt": partial_text},
                    returncode=1,
                ),
                self._codex_worker_runner(
                    files={"generated/result.txt": final_text},
                    report=dict(COMPLETION_REPORT),
                ),
            ),
            controller_runner=self._counting_controller_runner(calls),
            environment=self._test_environment(),
        )
        return request, outcome

    def _balance_chain_outcome(
        self,
        root: Path,
        *,
        controller_calls: list[str] | None = None,
        partial_text: str = "partial heavy progress\n",
        final_text: str = "terra completed result\n",
    ) -> tuple[DispatchRequest, dict[str, object]]:
        """Opus capacity exhaustion, then a verified DeepSeek balance failure."""

        request = self._request(root, route="opus")
        calls = [] if controller_calls is None else controller_calls
        outcome = run_staged_worker_with_continuation(
            self.config,
            request,
            runner=self._routed_runner(
                self._claude_worker_runner(
                    envelope=self._claude_envelope(
                        result="Claude usage limit reached. Your limit will reset at 3pm.",
                        is_error=True,
                        subtype="error_during_execution",
                    ),
                    files={"generated/result.txt": partial_text},
                    returncode=1,
                ),
                self._codex_provider_runner(
                    # DeepSeek fails before creating any staged output.
                    self._codex_failure_runner(stdout=self._deepseek_balance_envelope()),
                    self._codex_worker_runner(
                        files={"generated/result.txt": final_text},
                        report=dict(COMPLETION_REPORT),
                    ),
                ),
            ),
            controller_runner=self._counting_controller_runner(calls),
            environment=self._test_environment(),
        )
        return request, outcome

    def test_host_controller_and_reviewer_share_configured_binding(self) -> None:
        codex_controller = self.config.resolve_host_binding("codex", "controller")
        codex_reviewer = self.config.resolve_host_binding("codex", "reviewer")
        claude_controller = self.config.resolve_host_binding("claude", "controller")
        claude_reviewer = self.config.resolve_host_binding("claude", "reviewer")

        self.assertEqual(codex_reviewer.name, codex_controller.name)
        self.assertEqual(codex_reviewer, codex_controller)
        self.assertEqual(claude_reviewer.name, claude_controller.name)
        self.assertEqual(claude_reviewer, claude_controller)

    def test_canonical_skill_preserves_the_standing_authorization_record(self) -> None:
        skill_text = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
        for required_fragment in (
            "2026-09-05",
            "standing authorization",
            "`credential_file`",
            "`environment_key`",
            "does not disable platform review",
            "fail closed rather than silently using Terra",
        ):
            with self.subTest(required_fragment=required_fragment):
                self.assertIn(required_fragment, skill_text)

    def test_canonical_skill_records_the_claude_standing_authorization(self) -> None:
        # Markdown wraps prose across lines, so the authorization wording is
        # checked against whitespace-normalized text rather than raw bytes. The
        # required content itself is unchanged.
        skill_text = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())
        for required_fragment in (
            "2026-09-07",
            "允许",
            "standard Claude Code login",
            "claude-opus-5",
        ):
            with self.subTest(required_fragment=required_fragment):
                self.assertIn(required_fragment, skill_text)

    def test_config_rejects_disabling_the_independent_review_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            unsafe = Path(temp) / "delegation.toml"
            unsafe.write_text(
                CONFIG.read_text(encoding="utf-8").replace(
                    "require_independent_review_before_apply = true",
                    "require_independent_review_before_apply = false",
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_config(unsafe)

    def test_config_rejects_a_provider_without_a_display_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            unsafe = Path(temp) / "delegation.toml"
            provider = self.config.providers[
                self.config.resolve_worker_binding("deepseek").provider or ""
            ]
            unsafe.write_text(
                CONFIG.read_text(encoding="utf-8").replace(
                    f'name = "{provider["name"]}"\n', ""
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_config(unsafe)

    def test_config_rejects_a_default_timeout_above_its_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            unsafe = Path(temp) / "delegation.toml"
            default_timeout = int(self.config.policy["default_timeout_seconds"])
            invalid_timeout = int(self.config.policy["max_timeout_seconds"]) + 1
            unsafe.write_text(
                CONFIG.read_text(encoding="utf-8").replace(
                    f"default_timeout_seconds = {default_timeout}",
                    f"default_timeout_seconds = {invalid_timeout}",
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_config(unsafe)

    def test_config_rejects_unusable_heavy_route_references(self) -> None:
        cases = (
            ('heavy_primary = "opus"', 'heavy_primary = "gemini"'),
            (
                'heavy_exhaustion_fallback = "deepseek"',
                'heavy_exhaustion_fallback = "opus"',
            ),
        )
        for original, replacement in cases:
            with self.subTest(replacement=replacement):
                with tempfile.TemporaryDirectory() as temp:
                    unsafe = Path(temp) / "delegation.toml"
                    unsafe.write_text(
                        CONFIG.read_text(encoding="utf-8").replace(original, replacement),
                        encoding="utf-8",
                    )
                    with self.assertRaises(ConfigError):
                        load_config(unsafe)

    def test_config_route_chain_is_finite_and_never_cycles(self) -> None:
        """The two configured edges must form a forward-only terminating chain."""

        self.assertEqual(self.config.heavy_primary_route, "opus")
        self.assertEqual(self.config.heavy_exhaustion_fallback_route, "deepseek")
        self.assertEqual(self.config.insufficient_balance_route, "deepseek")
        self.assertEqual(self.config.insufficient_balance_fallback_route, "terra")

        self.assertEqual(
            self.config.continuation_edge("opus"),
            ("deepseek", frozenset({
                "token_quota_exhausted",
                "usage_rate_window_exhausted",
                "context_window_exhausted",
            })),
        )
        self.assertEqual(
            self.config.continuation_edge("deepseek"),
            ("terra", frozenset({"provider_insufficient_balance"})),
        )
        # Terra terminates the chain: it has no outgoing edge at all.
        self.assertEqual(self.config.continuation_edge("terra"), (None, frozenset()))

        # Walking the configured edges from any route reaches a terminal route
        # without repeating one.
        for start in ("opus", "deepseek", "terra"):
            with self.subTest(start=start):
                chain = [start]
                route: str | None = start
                while route is not None:
                    route, _ = self.config.continuation_edge(route)
                    if route is not None:
                        chain.append(route)
                self.assertEqual(len(set(chain)), len(chain))
                self.assertLessEqual(len(chain), len(self.config.workers))
                self.assertEqual(chain[-1], "terra")

        unusable = (
            # An unknown worker reference.
            ('insufficient_balance_fallback = "terra"', 'insufficient_balance_fallback = "gemini"'),
            # A self edge.
            ('insufficient_balance_fallback = "terra"', 'insufficient_balance_fallback = "deepseek"'),
            # A reverse edge that would close the opus -> deepseek -> opus cycle.
            ('insufficient_balance_fallback = "terra"', 'insufficient_balance_fallback = "opus"'),
            # A second outgoing edge on one route.
            ('insufficient_balance_route = "deepseek"', 'insufficient_balance_route = "opus"'),
        )
        for original, replacement in unusable:
            with self.subTest(replacement=replacement):
                with tempfile.TemporaryDirectory() as temp:
                    unsafe = Path(temp) / "delegation.toml"
                    unsafe.write_text(
                        CONFIG.read_text(encoding="utf-8").replace(original, replacement),
                        encoding="utf-8",
                    )
                    with self.assertRaises(ConfigError):
                        load_config(unsafe)

        # A balance route without a configured provider has no trusted evidence
        # source, and the terminal route may not gain an outgoing edge.
        with tempfile.TemporaryDirectory() as temp:
            unsafe = Path(temp) / "delegation.toml"
            unsafe.write_text(
                CONFIG.read_text(encoding="utf-8")
                .replace(
                    'insufficient_balance_route = "deepseek"',
                    'insufficient_balance_route = "terra"',
                )
                .replace(
                    'insufficient_balance_fallback = "terra"',
                    'insufficient_balance_fallback = "deepseek"',
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_config(unsafe)

    def test_one_config_edit_repoints_the_balance_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "delegation.toml"
            copied.write_text(
                CONFIG.read_text(encoding="utf-8")
                .replace('heavy_primary = "opus"', 'heavy_primary = "terra"')
                .replace(
                    'insufficient_balance_fallback = "terra"',
                    'insufficient_balance_fallback = "opus"',
                ),
                encoding="utf-8",
            )
            config = load_config(copied)
            self.assertEqual(config.insufficient_balance_fallback_route, "opus")
            self.assertEqual(config.continuation_edge("deepseek")[0], "opus")
            self.assertEqual(config.continuation_edge("opus"), (None, frozenset()))

    def test_one_config_edit_propagates_to_resolution_and_generated_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "delegation.toml"
            host_binding = self.config.resolve_host_binding("codex", "controller")
            terra_binding = self.config.resolve_worker_binding("terra")
            deepseek_binding = self.config.resolve_worker_binding("deepseek")
            provider = self.config.providers[deepseek_binding.provider or ""]
            copied.write_text(
                CONFIG.read_text(encoding="utf-8")
                .replace(f'model = "{host_binding.model}"', 'model = "test-host-model"')
                .replace(f'model = "{terra_binding.model}"', 'model = "test-standard-model"')
                .replace(f'effort = "{terra_binding.effort}"', 'effort = "test-standard-effort"')
                .replace(f'model = "{deepseek_binding.model}"', 'model = "test-large-model"')
                .replace(f'name = "{provider["name"]}"', 'name = "Test provider"')
                .replace(
                    f'wire_api = "{provider["wire_api"]}"',
                    'wire_api = "test-responses-wire"',
                ),
                encoding="utf-8",
            )
            config = load_config(copied)
            self.assertEqual(
                config.resolve_host_binding("codex", "reviewer").model,
                "test-host-model",
            )
            self.assertEqual(config.resolve_worker_binding("terra").model, "test-standard-model")
            self.assertEqual(config.resolve_worker_binding("terra").effort, "test-standard-effort")
            self.assertEqual(config.resolve_worker_binding("deepseek").model, "test-large-model")
            self.assertEqual(config.resolve_worker_binding("deepseek").provider, "deepseek")
            generated = generate_profile_text(config, "terra-worker")
            parsed = tomllib.loads(generated)
            self.assertEqual(parsed["model"], "test-standard-model")
            self.assertEqual(parsed["model_reasoning_effort"], "test-standard-effort")
            command = build_worker_command(
                config,
                "deepseek",
                Path(temp),
                workflow_selection="declined",
            )
            self.assertIn('model="test-large-model"', command)
            self.assertIn('model_providers.deepseek.name="Test provider"', command)
            self.assertIn('model_providers.deepseek.wire_api="test-responses-wire"', command)
            request = self._request(Path(temp) / "staged")
            receipt = run_staged_worker(config, request, dry_run=True)
            self.assertEqual(validate_receipt(config, receipt), [])

    def test_one_config_edit_repoints_the_heavy_primary_and_its_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "delegation.toml"
            copied.write_text(
                CONFIG.read_text(encoding="utf-8").replace(
                    'model = "claude-opus-5"\neffort = "xhigh"',
                    'model = "test-heavy-model"\neffort = "test-heavy-effort"',
                ),
                encoding="utf-8",
            )
            config = load_config(copied)
            binding = config.resolve_worker_binding("opus")
            self.assertEqual(binding.model, "test-heavy-model")
            self.assertEqual(binding.effort, "test-heavy-effort")
            self.assertEqual(binding.launcher, "claude")
            command = build_worker_command(
                config,
                "opus",
                Path(temp) / "stage",
                workflow_selection="declined",
                approved_outputs=("generated/result.txt",),
            )
            self.assertIn("test-heavy-model", command)
            self.assertIn("test-heavy-effort", command)

            rerouted = Path(temp) / "rerouted.toml"
            rerouted.write_text(
                CONFIG.read_text(encoding="utf-8")
                .replace('heavy_primary = "opus"', 'heavy_primary = "terra"')
                .replace(
                    'insufficient_balance_fallback = "terra"',
                    'insufficient_balance_fallback = "opus"',
                ),
                encoding="utf-8",
            )
            rerouted_config = load_config(rerouted)
            self.assertEqual(rerouted_config.heavy_primary_route, "terra")
            decision = route_workload(
                rerouted_config,
                minutes_low=None,
                minutes_high=None,
                confidence="high",
                large_work=True,
                large_work_reason="controller assessed a large integration",
                self_contained=False,
            )
            self.assertEqual(decision.route, "terra")

    def test_configured_worker_bindings_keep_their_launcher_boundaries(self) -> None:
        terra = self.config.resolve_worker_binding("terra")
        opus = self.config.resolve_worker_binding("opus")
        deepseek = self.config.resolve_worker_binding("deepseek")
        self.assertEqual((terra.launcher, terra.sandbox), ("codex", "workspace-write"))
        self.assertEqual((opus.launcher, opus.sandbox), ("claude", "workspace-write"))
        self.assertEqual((deepseek.launcher, deepseek.sandbox), ("codex", "workspace-write"))
        self.assertEqual(opus.model, "claude-opus-5")
        self.assertEqual(opus.effort, "xhigh")
        self.assertIsNone(opus.provider)
        self.assertEqual(deepseek.provider, "deepseek")
        self.assertEqual(self.config.heavy_primary_route, "opus")
        self.assertEqual(self.config.heavy_exhaustion_fallback_route, "deepseek")
        self.assertEqual(self.config.insufficient_balance_route, "deepseek")
        self.assertEqual(self.config.insufficient_balance_fallback_route, "terra")

    def test_clear_standard_work_routes_to_terra(self) -> None:
        decision = route_workload(
            self.config,
            minutes_low=11,
            minutes_high=27,
            confidence="high",
            large_work=False,
            large_work_reason=None,
            self_contained=False,
        )
        self.assertTrue(decision.dispatch)
        self.assertEqual(decision.route, "terra")
        self.assertIn("standard", decision.reason)

    def test_bounded_self_contained_work_stays_local(self) -> None:
        decision = route_workload(
            self.config,
            minutes_low=2,
            minutes_high=int(self.config.policy["local_max_minutes"]),
            confidence="high",
            large_work=False,
            large_work_reason=None,
            self_contained=True,
        )
        self.assertFalse(decision.dispatch)
        self.assertEqual(decision.route, "local")

    def test_below_threshold_work_uses_terra_without_confidence_clarification(self) -> None:
        for confidence in ("high", "plausible", "uncertain"):
            with self.subTest(confidence=confidence):
                decision = route_workload(
                    self.config,
                    minutes_low=11,
                    minutes_high=27,
                    confidence=confidence,
                    large_work=False,
                    large_work_reason=None,
                    self_contained=False,
                )
                self.assertTrue(decision.dispatch)
                self.assertEqual(decision.route, "terra")

    def test_near_or_plausibly_over_thirty_minutes_routes_to_the_heavy_primary(self) -> None:
        near = int(self.config.policy["heavy_near_minutes"])
        tolerance = int(self.config.policy["heavy_near_tolerance_minutes"])
        cases = (
            (near - tolerance, near - tolerance, "high", False, None),
            (near - 1, near - 1, "high", False, None),
            (near - 1, near + 5, "plausible", False, None),
            (None, None, "high", True, "controller assessed a large integration"),
        )
        for low, high, confidence, large_work, reason in cases:
            with self.subTest(low=low, high=high, large_work=large_work):
                decision = route_workload(
                    self.config,
                    minutes_low=low,
                    minutes_high=high,
                    confidence=confidence,
                    large_work=large_work,
                    large_work_reason=reason,
                    self_contained=False,
                )
                self.assertTrue(decision.dispatch)
                self.assertEqual(decision.route, self.config.heavy_primary_route)
                self.assertEqual(decision.route, "opus")
                self.assertNotEqual(
                    decision.route, self.config.heavy_exhaustion_fallback_route
                )

    def test_ambiguous_or_missing_material_facts_never_choose_a_random_worker(self) -> None:
        ambiguous = route_workload(
            self.config,
            minutes_low=21,
            minutes_high=29,
            confidence="uncertain",
            large_work=False,
            large_work_reason=None,
            self_contained=False,
        )
        self.assertFalse(ambiguous.dispatch)
        self.assertEqual(ambiguous.route, "needs_clarification")

        with self.assertRaises(RoutingInputError):
            route_workload(
                self.config,
                minutes_low=None,
                minutes_high=None,
                confidence="high",
                large_work=False,
                large_work_reason=None,
                self_contained=False,
            )

    def test_deepseek_command_is_child_specific_and_has_no_secret_value(self) -> None:
        command = build_worker_command(
            self.config,
            "deepseek",
            Path("/tmp/stage"),
            workflow_selection="declined",
        )
        rendered = " ".join(command)
        binding = self.config.resolve_worker_binding("deepseek")
        provider = self.config.providers[binding.provider or ""]
        self.assertEqual(command[:2], tuple(self.config.launchers["codex"]["command"]))
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ephemeral", command)
        self.assertNotIn("--sandbox", command)
        self.assertNotIn("sandbox_mode", rendered)
        self.assertNotIn("sandbox_workspace_write", rendered)
        self.assertIn('default_permissions="delegation_worker"', command)
        permission_table = next(item for item in command if item.startswith("permissions.delegation_worker="))
        self.assertIn('":root"="read"', permission_table)
        self.assertIn(f'"{Path("/tmp/stage").resolve()}"="write"', permission_table)
        self.assertIn("network={enabled=false}", permission_table)
        for setting in (
            "agents.enabled=false",
            "features.multi_agent=false",
            "features.apps=false",
            "features.hooks=false",
            'web_search="disabled"',
            'approval_policy="never"',
        ):
            self.assertIn(setting, command)
        self.assertIn(f'model_provider="{binding.provider}"', command)
        self.assertIn(f'model="{binding.model}"', command)
        self.assertIn(
            f'model_providers.{binding.provider}.name="{provider["name"]}"',
            command,
        )
        self.assertIn(
            f'model_providers.{binding.provider}.wire_api="{provider["wire_api"]}"',
            command,
        )
        self.assertIn(
            f'model_providers.{binding.provider}.env_key="{provider["environment_key"]}"',
            command,
        )
        self.assertNotIn(f'{provider["environment_key"]}=', rendered)
        self.assertNotIn("sk-", rendered)
        self.assertNotIn(str(provider["credential_file"]), rendered)
        self.assertIn("control/brief.txt", command[-1])
        self.assertNotIn("workspace/control/brief.txt", command[-1])
        self.assertIn("project-working-loop selection is already complete: declined", command[-1])
        self.assertIn("grants no other approval or authority", command[-1])
        self.assertIn("status='completed'", command[-1])
        self.assertIn("self_tests as a nonempty list", command[-1])

    def test_opus_worker_command_confines_file_tools_to_stage_and_approved_outputs(self) -> None:
        stage = Path("/tmp/opus-stage")
        command = build_worker_command(
            self.config,
            "opus",
            stage,
            workflow_selection="declined",
            approved_outputs=("generated/result.txt", "docs/notes.md"),
        )
        rendered = " ".join(command)
        binding = self.config.resolve_worker_binding("opus")
        launcher = self.config.launchers["claude"]
        resolved = stage.resolve()
        self.assertEqual(command[0], launcher["command"][0])
        self.assertIn("-p", command)
        self.assertIn("--model", command)
        self.assertIn(binding.model, command)
        self.assertIn("--effort", command)
        self.assertIn(binding.effort, command)
        for flag in (
            "--safe-mode",
            "--restricted",
            "--no-chrome",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--output-format",
        ):
            self.assertIn(flag, command)
        self.assertIn('{"mcpServers":{}}', command)
        self.assertIn("json", command)
        self.assertIn("--tools", command)
        self.assertIn("Read,Glob,Grep,Write,Edit", command)
        self.assertIn("--permission-mode", command)
        self.assertIn("dontAsk", command)
        rules = command[command.index("--allowedTools") + 1].split(",")
        self.assertIn(f"Read(/{resolved}/**)", rules)
        self.assertIn(f"Glob(/{resolved}/**)", rules)
        self.assertIn(f"Grep(/{resolved}/**)", rules)
        for relative in ("generated/result.txt", "docs/notes.md"):
            self.assertIn(f"Write(/{resolved}/outputs/{relative})", rules)
            self.assertIn(f"Edit(/{resolved}/outputs/{relative})", rules)
        self.assertTrue(all(rule.startswith(("Read(//", "Glob(//", "Grep(//", "Write(//", "Edit(//")) for rule in rules))
        # No shell, no nested agents, no global bypass, no bare mode, and no
        # emptied HOME: only the verified restricted flags appear.
        for forbidden in (
            "Bash",
            "--dangerously-skip-permissions",
            "--bare",
            "--output-last-message",
            "bypassPermissions",
            "acceptEdits",
        ):
            self.assertNotIn(forbidden, rendered)
        prompt = command[-1]
        self.assertIn(str(resolved / "workspace" / "control" / "brief.txt"), prompt)
        self.assertIn(str(resolved / "workspace" / "control" / "controller-plan.json"), prompt)
        self.assertIn(str(resolved / "inputs"), prompt)
        self.assertIn(str(resolved / "outputs"), prompt)
        self.assertIn(str(resolved / "reference"), prompt)
        # --restricted confines the child's file tools to its working directory,
        # so the prompt must report the stage root, not the workspace.
        self.assertIn(f"Your working directory is {resolved}.", prompt)
        self.assertNotIn(f"Your working directory is {resolved / 'workspace'}", prompt)
        self.assertIn("project-working-loop selection is already complete: declined", prompt)
        self.assertIn("Do not write a control file for your result", prompt)
        self.assertIn("restricted Claude file-tool", prompt)
        with self.assertRaises(BoundaryError):
            build_worker_command(
                self.config,
                "opus",
                stage,
                workflow_selection="declined",
            )
        with self.assertRaises(BoundaryError):
            build_worker_command(
                self.config,
                "opus",
                stage,
                workflow_selection=None,
                approved_outputs=("generated/result.txt",),
            )
        # A separator inside a rule would silently split it into two rules.
        for unsafe in ("generated/two words.txt", "generated/a,b.txt"):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(BoundaryError):
                    build_worker_command(
                        self.config,
                        "opus",
                        stage,
                        workflow_selection="declined",
                        approved_outputs=(unsafe,),
                    )

    def test_reviewer_commands_resolve_through_each_host_binding(self) -> None:
        codex = build_reviewer_command(
            self.config,
            "codex",
            Path("/tmp/review"),
            workflow_selection="declined",
        )
        claude = build_reviewer_command(
            self.config,
            "claude",
            Path("/tmp/review"),
            workflow_selection="declined",
        )
        codex_binding = self.config.resolve_host_binding("codex", "reviewer")
        claude_binding = self.config.resolve_host_binding("claude", "reviewer")
        self.assertIn(f'model="{codex_binding.model}"', codex)
        self.assertIn(f'model_reasoning_effort="{codex_binding.effort}"', codex)
        self.assertIn('default_permissions="delegation_reviewer"', codex)
        self.assertNotIn('default_permissions="delegation_worker"', codex)
        self.assertNotIn("--sandbox", codex)
        self.assertIn("agents.enabled=false", codex)
        self.assertIn('approval_policy="never"', codex)
        self.assertIn("review-evidence.json", codex[-1])
        self.assertIn("candidate-outputs", codex[-1])
        self.assertIn("project-working-loop selection is already complete: declined", codex[-1])
        self.assertIn("grants no other approval or authority", codex[-1])
        self.assertIn(claude_binding.model, claude)
        self.assertIn(claude_binding.effort, claude)
        self.assertIn("--no-chrome", claude)
        self.assertIn("--permission-mode", claude)
        self.assertIn("plan", claude)
        self.assertIn("-p", claude)
        self.assertIn("Read,Glob,Grep", claude)
        self.assertNotIn("Read,Glob,Grep,Write,Edit", claude)
        self.assertIn("project-working-loop selection is already complete: declined", claude[-1])
        self.assertIn("evidence as a nonempty list of strings", claude[-1])

    def test_fresh_controller_plan_uses_the_host_binding_and_binds_the_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp), controller_host="codex")
            stage = prepare_stage(request)
            captured: dict[str, tuple[str, ...]] = {}

            def controller_runner(
                command: tuple[str, ...], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                captured[request.controller_host] = command
                manifest = json.loads(
                    (Path(str(kwargs["cwd"])) / "control" / "controller-request.json").read_text(
                        encoding="utf-8"
                    )
                )
                outcome = {
                    "status": "approved",
                    "route": manifest["route"],
                    "request_manifest": manifest,
                    "plan_steps": ["write only the requested staged result"],
                    "evidence": ["controller assessed the route and explicit inputs"],
                    "limitations": ["no source tree write permission"],
                    "fresh_process": True,
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(outcome), "")

            controller = run_controller_planner(
                self.config,
                request,
                stage,
                runner=controller_runner,
                environment={"PATH": "/usr/bin"},
            )
            self.assertEqual(validate_controller_receipt(self.config, controller), [])
            self.assertEqual(controller["status"], "approved")
            self.assertEqual(controller["request_manifest"]["controller_host"], "codex")
            self.assertNotIn("continuation", controller["request_manifest"])
            codex = captured["codex"]
            codex_binding = self.config.resolve_host_binding("codex", "controller")
            self.assertEqual(codex[: len(self.config.launchers["codex"]["command"])], tuple(self.config.launchers["codex"]["command"]))
            self.assertIn(f'model="{codex_binding.model}"', codex)
            self.assertIn(f'model_reasoning_effort="{codex_binding.effort}"', codex)
            self.assertIn('default_permissions="delegation_reviewer"', codex)
            self.assertIn("agents.enabled=false", codex)
            self.assertIn("project-working-loop selection is already complete: declined", codex[-1])
            self.assertIn("plan_steps as a nonempty list", codex[-1])

        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp), controller_host="claude")
            stage = prepare_stage(request)
            captured = {}

            def claude_runner(
                command: tuple[str, ...], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                captured["claude"] = command
                manifest = json.loads(
                    (Path(str(kwargs["cwd"])) / "control" / "controller-request.json").read_text(
                        encoding="utf-8"
                    )
                )
                outcome = {
                    "status": "approved",
                    "route": manifest["route"],
                    "request_manifest": manifest,
                    "plan_steps": ["write only the requested staged result"],
                    "evidence": ["controller assessed the route and explicit inputs"],
                    "limitations": [],
                    "fresh_process": True,
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(outcome), "")

            controller = run_controller_planner(
                self.config,
                request,
                stage,
                runner=claude_runner,
                environment={"PATH": "/usr/bin"},
            )
            self.assertEqual(validate_controller_receipt(self.config, controller), [])
            claude = captured["claude"]
            claude_binding = self.config.resolve_host_binding("claude", "controller")
            self.assertIn(claude_binding.model, claude)
            self.assertIn(claude_binding.effort, claude)
            self.assertIn("Read,Glob,Grep", claude)
            self.assertIn("project-working-loop selection is already complete: declined", claude[-1])

    def test_unapproved_controller_plan_never_starts_the_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp))
            worker_started = False

            def rejected_controller(
                command: tuple[str, ...], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                manifest = json.loads(
                    (Path(str(kwargs["cwd"])) / "control" / "controller-request.json").read_text(
                        encoding="utf-8"
                    )
                )
                outcome = {
                    "status": "rejected",
                    "route": manifest["route"],
                    "request_manifest": manifest,
                    "plan_steps": ["scope must be revised by the parent"],
                    "evidence": ["requested route is unsuitable"],
                    "limitations": [],
                    "fresh_process": True,
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(outcome), "")

            def worker_runner(*_args: object, **_kwargs: object) -> object:
                nonlocal worker_started
                worker_started = True
                raise AssertionError("worker must not run after controller rejection")

            receipt = run_staged_worker(
                self.config,
                request,
                runner=worker_runner,
                controller_runner=rejected_controller,
                environment=self._test_environment(),
            )
            self.assertFalse(worker_started)
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["errors"], ["controller_plan_not_approved"])
            self.assertEqual(receipt["controller_receipt"]["status"], "rejected")
            self.assertEqual(validate_receipt(self.config, receipt), [])

    def test_session_workflow_selection_is_explicit_and_per_dispatch(self) -> None:
        enabled_worker = build_worker_command(
            self.config,
            "terra",
            Path("/tmp/stage"),
            workflow_selection="enabled",
        )
        enabled_reviewer = build_reviewer_command(
            self.config,
            "codex",
            Path("/tmp/review"),
            workflow_selection="enabled",
        )
        enabled_opus = build_worker_command(
            self.config,
            "opus",
            Path("/tmp/stage"),
            workflow_selection="enabled",
            approved_outputs=("generated/result.txt",),
        )
        self.assertIn("project-working-loop selection is already complete: enabled", enabled_worker[-1])
        self.assertIn("project-working-loop selection is already complete: enabled", enabled_reviewer[-1])
        self.assertIn("project-working-loop selection is already complete: enabled", enabled_opus[-1])

        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp), workflow_selection="")
            with self.assertRaises(BoundaryError):
                prepare_stage(request)
            self.assertFalse(request.stage_root.exists())
        with self.assertRaises(BoundaryError):
            build_worker_command(
                self.config,
                "terra",
                Path("/tmp/stage"),
                workflow_selection=None,
            )
        with self.assertRaises(BoundaryError):
            build_reviewer_command(
                self.config,
                "codex",
                Path("/tmp/review"),
                workflow_selection=None,
            )

    def test_staging_accepts_only_explicit_inputs_and_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp))
            stage = prepare_stage(request)
            self.assertEqual(
                (stage.inputs / "notes.txt").read_text(encoding="utf-8"),
                "approved input\n",
            )
            self.assertFalse((stage.workspace / "unrelated.txt").exists())
            self.assertFalse((stage.root / "reference").exists())
            self.assertEqual(stage.approved_outputs, ("generated/result.txt",))

    def test_staging_rejects_path_escape_and_symlink_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = self._request(root, inputs=("../secret.txt",))
            with self.assertRaises(BoundaryError):
                prepare_stage(request)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = self._request(root)
            outside = root / "outside.txt"
            outside.write_text("not staged", encoding="utf-8")
            (request.source_root / "linked.txt").symlink_to(outside)
            linked = DispatchRequest(
                route=request.route,
                source_root=request.source_root,
                stage_root=request.stage_root,
                inputs=("linked.txt",),
                approved_outputs=request.approved_outputs,
                brief=request.brief,
                timeout_seconds=request.timeout_seconds,
                workflow_selection=request.workflow_selection,
                controller_host=request.controller_host,
            )
            with self.assertRaises(BoundaryError):
                prepare_stage(linked)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = self._request(root)
            (request.source_root / ".git").mkdir()
            nested_stage = DispatchRequest(
                route=request.route,
                source_root=request.source_root,
                stage_root=request.source_root / ".delegation-stage",
                inputs=request.inputs,
                approved_outputs=request.approved_outputs,
                brief=request.brief,
                timeout_seconds=request.timeout_seconds,
                workflow_selection=request.workflow_selection,
                controller_host=request.controller_host,
            )
            with self.assertRaises(BoundaryError):
                prepare_stage(nested_stage)

        with tempfile.TemporaryDirectory() as temp:
            repository = Path(temp) / "repository"
            repository.mkdir()
            (repository / ".git").mkdir()
            source = repository / "nested-source"
            source.mkdir()
            (source / "notes.txt").write_text("approved input\n", encoding="utf-8")
            nested_source_stage = DispatchRequest(
                route="terra",
                source_root=source,
                stage_root=repository / ".delegation-stage",
                inputs=("notes.txt",),
                approved_outputs=("generated/result.txt",),
                brief="Write only the declared staged output.",
                timeout_seconds=30,
                workflow_selection="declined",
                controller_host="codex",
            )
            with self.assertRaises(BoundaryError):
                prepare_stage(nested_source_stage)

    def test_nested_dispatch_is_refused_before_a_stage_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp), nested_dispatch_requested=True)
            with self.assertRaises(BoundaryError):
                prepare_stage(request)
            self.assertFalse(request.stage_root.exists())

    def test_direct_worker_cap_is_enforced_by_one_shared_controller_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            ledger = Path(temp) / "ledger"
            leases = [
                acquire_direct_worker_lease(self.config, ledger, f"worker-{index}")
                for index in range(int(self.config.policy["direct_worker_cap"]))
            ]
            try:
                with self.assertRaisesRegex(BoundaryError, "direct_worker_cap_reached"):
                    acquire_direct_worker_lease(self.config, ledger, "worker-over-cap")
            finally:
                for lease in leases:
                    lease.release()

    def test_dry_run_does_not_call_a_model_or_copy_outputs_to_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp))

            def runner(*_args: object, **_kwargs: object) -> object:
                raise AssertionError("dry-run must not invoke the runner")

            receipt = run_staged_worker(
                self.config,
                request,
                runner=runner,
                dry_run=True,
            )
            self.assertEqual(receipt["status"], "planned")
            self.assertEqual(receipt["actual_model"], "unobserved")
            self.assertEqual(receipt["actual_effort"], "unobserved")
            self.assertFalse((request.source_root / "generated" / "result.txt").exists())
            self.assertEqual(validate_receipt(self.config, receipt), [])

    def test_dry_run_heavy_dispatch_never_evaluates_a_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp), route="opus")

            def runner(*_args: object, **_kwargs: object) -> object:
                raise AssertionError("dry-run must not invoke the runner")

            outcome = run_staged_worker_with_continuation(
                self.config,
                request,
                runner=runner,
                dry_run=True,
            )
            self.assertEqual(outcome["status"], "planned")
            self.assertFalse(outcome["continuation"]["attempted"])
            self.assertIsNone(outcome["continuation"]["receipt"])
            self.assertEqual(outcome["primary"]["permission_profile"], "claude_restricted_file_tools")
            self.assertEqual(outcome["primary"]["write_boundary"], "approved_output_paths_only")
            self.assertEqual(validate_receipt(self.config, outcome["primary"]), [])
            self.assertEqual(effective_worker_receipt(outcome), outcome["primary"])

    def test_core_rejects_a_timeout_outside_the_configured_cap_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            request = self._request(
                Path(temp),
                timeout_seconds=int(self.config.policy["max_timeout_seconds"]) + 1,
            )
            with self.assertRaises(BoundaryError):
                run_staged_worker(self.config, request, dry_run=True)
            self.assertFalse(request.stage_root.exists())

    def test_missing_provider_environment_fails_closed_before_a_child_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            default_credential_file = self.config.providers[
                self.config.resolve_worker_binding("deepseek").provider or ""
            ]["credential_file"]
            no_fallback_path = root / "delegation.toml"
            no_fallback_path.write_text(
                CONFIG.read_text(encoding="utf-8").replace(
                    f"credential_file = {json.dumps(str(default_credential_file))}\n",
                    "",
                ),
                encoding="utf-8",
            )
            config = load_config(no_fallback_path)
            request = self._request(root / "case")
            provider = config.providers[
                config.resolve_worker_binding("deepseek").provider or ""
            ]

            def runner(*_args: object, **_kwargs: object) -> object:
                raise AssertionError("missing provider environment must block launch")

            receipt = run_staged_worker(
                config,
                request,
                runner=runner,
                environment={"PATH": "/usr/bin"},
            )
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(
                receipt["errors"],
                [f'missing_provider_environment:{provider["environment_key"]}'],
            )
            self.assertEqual(receipt["failure_category"], "generic_failure")
            self.assertNotIn("test-only-token", json.dumps(receipt))

    def test_provider_environment_precedes_credential_file_without_reading_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider = self.config.providers[
                self.config.resolve_worker_binding("deepseek").provider or ""
            ]
            key_name = provider["environment_key"]
            _, config = self._credential_config(root, root / "missing.env")
            request = self._request(root / "case")
            captured: dict[str, dict[str, str]] = {}

            def runner(
                command: tuple[str, ...], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                captured["worker"] = dict(kwargs["env"])  # type: ignore[arg-type]
                final_path = Path(command[command.index("--output-last-message") + 1])
                output = final_path.parents[2] / "outputs" / "generated" / "result.txt"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("staged result\n", encoding="utf-8")
                final_path.write_text(
                    json.dumps(
                        {
                            "status": "completed",
                            "changed_outputs": ["generated/result.txt"],
                            "self_tests": ["python -m unittest"],
                            "limitations": [],
                            "nested_dispatch": "not_attempted",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            receipt = run_staged_worker(
                config,
                request,
                runner=runner,
                controller_runner=self._approved_controller_runner,
                environment={"PATH": "/usr/bin", key_name: "env-secret"},
            )
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(captured["worker"][key_name], "env-secret")
            self.assertNotIn("env-secret", json.dumps(receipt))

    def test_credential_file_fallback_is_literal_and_confined_to_worker_auth(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            credential_file = root / "deepseek.env"
            credential_file.write_text(
                "# synthetic only\n"
                "export OTHER_KEY = ignored\n"
                "DEEPSEEK_API_KEY='fallback-secret' # inline comment\n",
                encoding="utf-8",
            )
            _, config = self._credential_config(root, credential_file)
            provider = config.providers[
                config.resolve_worker_binding("deepseek").provider or ""
            ]
            key_name = provider["environment_key"]
            request = self._request(root / "case")
            captured: dict[str, dict[str, str]] = {}

            def controller_runner(
                command: tuple[str, ...], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                captured["controller"] = dict(kwargs["env"])  # type: ignore[arg-type]
                return self._approved_controller_runner(command, **kwargs)

            def worker_runner(
                command: tuple[str, ...], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                captured["worker"] = dict(kwargs["env"])  # type: ignore[arg-type]
                final_path = Path(command[command.index("--output-last-message") + 1])
                output = final_path.parents[2] / "outputs" / "generated" / "result.txt"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("staged result\n", encoding="utf-8")
                final_path.write_text(
                    json.dumps(
                        {
                            "status": "completed",
                            "changed_outputs": ["generated/result.txt"],
                            "self_tests": ["python -m unittest"],
                            "limitations": [],
                            "nested_dispatch": "not_attempted",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            receipt = run_staged_worker(
                config,
                request,
                runner=worker_runner,
                controller_runner=controller_runner,
                environment={"PATH": "/usr/bin"},
            )
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(captured["worker"][key_name], "fallback-secret")
            self.assertNotIn("OTHER_KEY", captured["worker"])
            self.assertNotIn(key_name, captured["controller"])
            self.assertNotIn("fallback-secret", captured["controller"])
            self.assertNotIn("fallback-secret", json.dumps(receipt))

    def test_credential_file_values_are_not_shell_expanded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marker = root / "shell-executed"
            credential_file = root / "deepseek.env"
            literal = f"$(touch {marker})"
            credential_file.write_text(f'DEEPSEEK_API_KEY="{literal}"\n', encoding="utf-8")
            _, config = self._credential_config(root, credential_file)
            provider = config.providers[
                config.resolve_worker_binding("deepseek").provider or ""
            ]
            key_name = provider["environment_key"]
            request = self._request(root / "case")
            captured: dict[str, str] = {}

            def runner(
                command: tuple[str, ...], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                captured[key_name] = dict(kwargs["env"])[key_name]  # type: ignore[arg-type]
                final_path = Path(command[command.index("--output-last-message") + 1])
                output = final_path.parents[2] / "outputs" / "generated" / "result.txt"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("staged result\n", encoding="utf-8")
                final_path.write_text(
                    json.dumps(
                        {
                            "status": "completed",
                            "changed_outputs": ["generated/result.txt"],
                            "self_tests": ["python -m unittest"],
                            "nested_dispatch": "not_attempted",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            receipt = run_staged_worker(
                config,
                request,
                runner=runner,
                controller_runner=self._approved_controller_runner,
                environment={"PATH": "/usr/bin"},
            )
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(captured[key_name], literal)
            self.assertFalse(marker.exists())

    def test_credential_file_failures_are_sanitized(self) -> None:
        cases = (
            ("missing.env", None, "credential_file_unreadable"),
            ("malformed.env", "DEEPSEEK_API_KEY no_equals\n", "credential_file_malformed"),
            ("missing-key.env", "OTHER_KEY=value\n", "credential_file_missing_key"),
            (
                "duplicate.env",
                "DEEPSEEK_API_KEY=one\nDEEPSEEK_API_KEY=two\n",
                "credential_file_duplicate_key",
            ),
            ("empty.env", "DEEPSEEK_API_KEY=\n", "credential_file_empty"),
        )
        for filename, contents, expected_error in cases:
            with self.subTest(filename=filename):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    credential_file = root / filename
                    if contents is not None:
                        credential_file.write_text(contents, encoding="utf-8")
                    _, config = self._credential_config(root, credential_file)
                    request = self._request(root / "case")

                    def runner(*_args: object, **_kwargs: object) -> object:
                        raise AssertionError("credential-file preflight must block launch")

                    receipt = run_staged_worker(
                        config,
                        request,
                        runner=runner,
                        environment={"PATH": "/usr/bin"},
                    )
                    self.assertEqual(receipt["status"], "failed")
                    self.assertEqual(receipt["errors"], [expected_error])

    def test_invalid_utf8_credential_file_is_sanitized_and_blocks_worker_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            credential_file = root / "invalid.env"
            provider = self.config.providers[
                self.config.resolve_worker_binding("deepseek").provider or ""
            ]
            key_name = provider["environment_key"]
            credential_file.write_bytes(b"DEEPSEEK_API_KEY=synthetic-test-only\xff\n")

            with self.assertRaises(BoundaryError) as direct_error:
                _dotenv_target_value(credential_file, key_name)
            self.assertEqual(str(direct_error.exception), "credential_file_malformed")
            self.assertIsNone(direct_error.exception.__cause__)

            _, config = self._credential_config(root, credential_file)
            request = self._request(root / "case")
            invoked = {"worker": False, "controller": False}

            def runner(
                command: tuple[str, ...], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                invoked["worker"] = True
                raise AssertionError("invalid UTF-8 credential file must block worker launch")

            def controller_runner(
                command: tuple[str, ...], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                invoked["controller"] = True
                raise AssertionError("invalid UTF-8 credential file must block controller launch")

            receipt = run_staged_worker(
                config,
                request,
                runner=runner,
                controller_runner=controller_runner,
                environment={"PATH": "/usr/bin"},
            )
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["errors"], ["credential_file_malformed"])
            self.assertFalse(invoked["worker"])
            self.assertFalse(invoked["controller"])
            serialized = json.dumps(receipt)
            self.assertNotIn("synthetic-test-only", serialized)
            self.assertNotIn(b"\xff", serialized.encode("utf-8"))

    def test_terra_never_reads_provider_credential_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, config = self._credential_config(root, root / "missing.env")
            provider = config.providers[
                config.resolve_worker_binding("deepseek").provider or ""
            ]
            key_name = provider["environment_key"]
            request = self._request(root / "case", route="terra")
            captured: dict[str, str] = {}

            def runner(
                command: tuple[str, ...], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                captured.update(dict(kwargs["env"]))  # type: ignore[arg-type]
                final_path = Path(command[command.index("--output-last-message") + 1])
                output = final_path.parents[2] / "outputs" / "generated" / "result.txt"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("staged result\n", encoding="utf-8")
                final_path.write_text(
                    json.dumps(
                        {
                            "status": "completed",
                            "changed_outputs": ["generated/result.txt"],
                            "self_tests": ["python -m unittest"],
                            "nested_dispatch": "not_attempted",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            receipt = run_staged_worker(
                config,
                request,
                runner=runner,
                controller_runner=self._approved_controller_runner,
                environment={"PATH": "/usr/bin"},
            )
            self.assertEqual(receipt["status"], "completed")
            self.assertNotIn(key_name, captured)

    def test_reviewer_environment_never_exposes_provider_fallback_credentials(self) -> None:
        provider = self.config.providers[
            self.config.resolve_worker_binding("deepseek").provider or ""
        ]
        key_name = provider["environment_key"]
        reviewer_env = _reviewer_environment(
            {
                "PATH": "/usr/bin",
                key_name: "reviewer-secret",
                "OTHER_KEY": "ignored",
            }
        )
        self.assertNotIn(key_name, reviewer_env)
        self.assertNotIn("OTHER_KEY", reviewer_env)
        self.assertNotIn("reviewer-secret", reviewer_env)

    def test_terra_preserves_supported_auth_environment_without_recording_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp), route="terra")
            supplied = {
                "PATH": "/usr/bin",
                "HOME": "/synthetic/home",
                "CODEX_HOME": "/synthetic/codex",
                "OPENAI_API_KEY": "synthetic-openai-auth",
                "CODEX_API_KEY": "synthetic-codex-auth",
            }

            def runner(
                command: tuple[str, ...], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                environment = kwargs["env"]
                self.assertIsInstance(environment, dict)
                for key, value in supplied.items():
                    self.assertEqual(environment[key], value)
                final_path = Path(command[command.index("--output-last-message") + 1])
                output = final_path.parents[2] / "outputs" / "generated" / "result.txt"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("staged result\n", encoding="utf-8")
                final_path.write_text(
                    json.dumps(
                        {
                            "status": "completed",
                            "changed_outputs": ["generated/result.txt"],
                            "self_tests": ["python -m unittest"],
                            "limitations": ["no source-tree write was attempted"],
                            "nested_dispatch": "not_attempted",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            receipt = run_staged_worker(
                self.config,
                request,
                runner=runner,
                controller_runner=self._approved_controller_runner,
                environment=supplied,
            )
            self.assertEqual(receipt["status"], "completed")
            serialized = json.dumps(receipt)
            self.assertNotIn("synthetic-openai-auth", serialized)
            self.assertNotIn("synthetic-codex-auth", serialized)

    def test_opus_worker_result_envelope_is_parsed_persisted_and_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp), route="opus")
            captured: dict[str, object] = {}
            fenced_report = (
                "Finished the staged work.\n\n```json\n"
                + json.dumps(dict(COMPLETION_REPORT), indent=2)
                + "\n```\n"
            )
            supplied = {
                "PATH": "/usr/bin",
                "HOME": "/synthetic/claude-home",
                "CODEX_HOME": "/synthetic/codex",
                "OPENAI_API_KEY": "synthetic-openai-auth",
                "DEEPSEEK_API_KEY": "synthetic-deepseek-auth",
            }
            receipt = run_staged_worker(
                self.config,
                request,
                runner=self._claude_worker_runner(
                    envelope=self._claude_envelope(
                        result=fenced_report,
                        model_usage={
                            "claude-opus-5": {"inputTokens": 42},
                            "claude-haiku-4-5-20251001": {"inputTokens": 9},
                        },
                    ),
                    files={"generated/result.txt": "staged result\n"},
                    captured=captured,
                ),
                controller_runner=self._approved_controller_runner,
                environment=supplied,
            )
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(validate_receipt(self.config, receipt), [])
            self.assertEqual(receipt["self_tests"], ["python -m unittest"])
            self.assertEqual(receipt["changed_outputs"], ["generated/result.txt"])
            self.assertEqual(receipt["failure_category"], "none")
            self.assertEqual(receipt["launcher"], "claude")
            self.assertEqual(receipt["permission_profile"], "claude_restricted_file_tools")
            self.assertEqual(receipt["write_boundary"], "approved_output_paths_only")

            # The controller persists the structured report; the child is never
            # asked to write a control file.
            stage_root = Path(receipt["stage_root"])
            persisted = json.loads(
                (stage_root / "workspace" / "control" / "final-message.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(persisted["status"], "completed")
            self.assertEqual(persisted["changed_outputs"], ["generated/result.txt"])

            # Requested and observed values stay separate, and internal Haiku
            # accounting is never reported as the primary model.
            self.assertEqual(receipt["requested_model"], "claude-opus-5")
            self.assertEqual(receipt["requested_effort"], "xhigh")
            self.assertEqual(receipt["actual_model"], "claude-opus-5")
            self.assertEqual(receipt["actual_effort"], "unobserved")
            self.assertEqual(receipt["observed_model_usage"]["primary_model"], "claude-opus-5")
            self.assertEqual(
                receipt["observed_model_usage"]["auxiliary_models"],
                ["claude-haiku-4-5-20251001"],
            )
            self.assertEqual(receipt["observed_model_usage"]["effort"], "unobserved")

            environment = captured["env"]
            self.assertEqual(environment["HOME"], "/synthetic/claude-home")
            for excluded in ("CODEX_HOME", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
                self.assertNotIn(excluded, environment)
            self.assertNotIn("synthetic-deepseek-auth", json.dumps(receipt))
            self.assertEqual(captured["cwd"], str(stage_root))
            self.assertIn(
                f"Write(/{stage_root}/outputs/generated/result.txt)",
                receipt["file_tool_rules"],
            )

    def test_claude_worker_keeps_the_standard_os_login_environment_and_stage_cwd(self) -> None:
        """A launcher regression test for the observed logged-out failure.

        With only PATH/LANG/LC_ALL/TERM/HOME a controlled read-only diagnostic
        reported the Claude CLI as logged out; the ordinary OS session keys
        below restored the existing first-party login. All values here are
        synthetic, and no CLAUDE*/ANTHROPIC* or provider credential is invented
        or forwarded.
        """

        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp), route="opus")
            captured: dict[str, object] = {}
            preserved = {
                "PATH": "/synthetic/bin",
                "LANG": "en_US.UTF-8",
                "LC_ALL": "en_US.UTF-8",
                "TERM": "xterm-256color",
                "HOME": "/synthetic/claude-home",
                "USER": "synthetic-user",
                "LOGNAME": "synthetic-user",
                "SHELL": "/bin/zsh",
                "TMPDIR": "/synthetic/tmp/",
                "SECURITYSESSIONID": "synthetic-session-id",
                "__CF_USER_TEXT_ENCODING": "0x1F5:0x0:0x0",
            }
            excluded = {
                "CODEX_HOME": "/synthetic/codex",
                "OPENAI_API_KEY": "synthetic-openai-auth",
                "CODEX_API_KEY": "synthetic-codex-auth",
                "DEEPSEEK_API_KEY": "synthetic-deepseek-auth",
                "ANTHROPIC_API_KEY": "synthetic-anthropic-auth",
                "CLAUDE_CODE_TOKEN": "synthetic-claude-token",
                "UNRELATED_SETTING": "synthetic-unrelated",
            }
            self.assertEqual(set(CLAUDE_ENVIRONMENT_KEYS), set(preserved))

            receipt = run_staged_worker(
                self.config,
                request,
                runner=self._claude_worker_runner(
                    envelope=self._claude_envelope(
                        result=json.dumps(dict(COMPLETION_REPORT))
                    ),
                    files={"generated/result.txt": "staged result\n"},
                    captured=captured,
                ),
                controller_runner=self._approved_controller_runner,
                environment={**preserved, **excluded},
            )
            self.assertEqual(receipt["status"], "completed")
            environment = captured["env"]
            self.assertIsInstance(environment, dict)
            self.assertEqual(environment, preserved)
            serialized = json.dumps(receipt)
            for key, value in excluded.items():
                with self.subTest(excluded=key):
                    self.assertNotIn(key, environment)
                    self.assertNotIn(value, serialized)

            # --restricted confines the file tools to the working directory, and
            # inputs/outputs/reference/workspace are siblings under the stage
            # root, so the worker must start there with absolute staged paths.
            stage_root = Path(receipt["stage_root"])
            self.assertEqual(captured["cwd"], str(stage_root))
            prompt = tuple(captured["command"])[-1]  # type: ignore[index]
            self.assertIn(f"Your working directory is {stage_root}.", prompt)
            self.assertIn(str(stage_root / "workspace" / "control" / "brief.txt"), prompt)
            self.assertIn(
                str(stage_root / "workspace" / "control" / "controller-plan.json"), prompt
            )
            self.assertIn(str(stage_root / "inputs"), prompt)
            self.assertIn(str(stage_root / "outputs"), prompt)

    def test_codex_worker_cwd_contract_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp), route="terra")
            captured: dict[str, object] = {}
            receipt = run_staged_worker(
                self.config,
                request,
                runner=self._codex_worker_runner(
                    files={"generated/result.txt": "staged result\n"},
                    report=dict(COMPLETION_REPORT),
                    captured=captured,
                ),
                controller_runner=self._approved_controller_runner,
                environment=self._test_environment(),
            )
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(
                captured["cwd"], str(Path(receipt["stage_root"]) / "workspace")
            )
            prompt = tuple(captured["command"])[-1]  # type: ignore[index]
            self.assertIn("control/brief.txt", prompt)
            self.assertIn("../inputs", prompt)

    def test_haiku_only_accounting_never_becomes_the_primary_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp), route="opus")
            receipt = run_staged_worker(
                self.config,
                request,
                runner=self._claude_worker_runner(
                    envelope=self._claude_envelope(
                        result=json.dumps(dict(COMPLETION_REPORT)),
                        model_usage={"claude-haiku-4-5-20251001": {"inputTokens": 9}},
                    ),
                    files={"generated/result.txt": "staged result\n"},
                ),
                controller_runner=self._approved_controller_runner,
                environment=self._test_environment(),
            )
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["actual_model"], "unobserved")
            self.assertEqual(receipt["observed_model_usage"]["primary_model"], "unobserved")
            self.assertEqual(
                receipt["observed_model_usage"]["auxiliary_models"],
                ["claude-haiku-4-5-20251001"],
            )
            self.assertEqual(validate_receipt(self.config, receipt), [])

    def test_capacity_failure_classification_reads_only_failure_envelopes(self) -> None:
        eligible = (
            (
                "token_quota_exhausted",
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "result": "API Error: insufficient quota for this workspace",
                },
            ),
            (
                "usage_rate_window_exhausted",
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "result": "Claude usage limit reached. Your limit will reset at 3pm.",
                },
            ),
            (
                "usage_rate_window_exhausted",
                # Observed installed-client shape: a failed result can retain
                # subtype=success while reporting a real session limit.
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "result": "You've hit your session limit · resets 6:10pm (America/New_York)",
                },
            ),
            (
                "usage_rate_window_exhausted",
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "errors": ["You've hit your monthly spend limit"],
                },
            ),
            (
                "usage_rate_window_exhausted",
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "errors": [{"message": "You've hit your session limit"}],
                },
            ),
            (
                "context_window_exhausted",
                {
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "prompt is too long: 250000 tokens > 200000",
                    },
                },
            ),
        )
        for expected, envelope in eligible:
            with self.subTest(expected=expected):
                self.assertEqual(classify_capacity_failure(envelope), expected)

        excluded = (
            {
                "is_error": True,
                "subtype": "error_during_execution",
                "result": "Authentication failed; run /login. Your usage limit is fine.",
            },
            {
                "is_error": True,
                "subtype": "error_during_execution",
                "result": "Permission denied writing outside the approved outputs; quota unrelated",
            },
            {
                "is_error": True,
                "subtype": "error_during_execution",
                "result": "network error ECONNREFUSED while contacting the provider quota service",
            },
            {
                "is_error": True,
                "subtype": "error_during_execution",
                "result": "request timed out after 600s; rate limit not reported",
            },
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Billing address is required",
                },
            },
            {
                "type": "result",
                "is_error": True,
                "subtype": "error_during_execution",
                "errors": ["The local token budget was exhausted"],
            },
            {
                "type": "result",
                "is_error": True,
                "subtype": "error_during_execution",
                "errors": [{"message": "Billing address is required"}],
            },
            {"is_error": True, "subtype": "error_max_turns", "result": "usage limit reached"},
            {"is_error": True, "subtype": "error_max_tokens", "result": "context window exceeded"},
            {
                "is_error": False,
                "subtype": "success",
                "result": "I hit my context window and quota while thinking, but finished anyway.",
            },
            {
                "type": "result",
                "is_error": False,
                "subtype": "success",
                "errors": ["You've hit your monthly spend limit"],
            },
            {"is_error": False, "subtype": "success", "result": json.dumps(COMPLETION_REPORT)},
            None,
            {},
        )
        for envelope in excluded:
            with self.subTest(envelope=envelope):
                self.assertIsNone(classify_capacity_failure(envelope))

    def test_insufficient_balance_needs_trusted_configured_provider_evidence(self) -> None:
        deepseek = self.config.resolve_worker_binding("deepseek")
        terra = self.config.resolve_worker_binding("terra")
        provider = self.config.providers[deepseek.provider or ""]
        endpoint = str(provider["base_url"])
        actual_evidence = self._deepseek_balance_envelope()

        eligible = (
            # The actual observed top-level Codex turn.failed envelope.
            ("actual_turn_failed", actual_evidence),
            # The same failure reported as one flat error envelope message.
            (
                "flat_error_message",
                json.dumps(
                    {
                        "type": "error",
                        "message": (
                            "unexpected status 402 Payment Required: Insufficient "
                            f"Balance, url: {endpoint}/responses"
                        ),
                    }
                ),
            ),
            # An equivalent structured verified provider code.
            (
                "structured_code",
                json.dumps(
                    {
                        "type": "turn.failed",
                        "error": {
                            "code": "insufficient_balance",
                            "status": 402,
                            "url": f"{endpoint}/responses",
                        },
                    }
                ),
            ),
            # Streamed events before the failure envelope change nothing.
            (
                "streamed_then_failed",
                "\n".join(
                    [
                        json.dumps({"type": "thread.started"}),
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {"type": "agent_message", "text": "working"},
                            }
                        ),
                        actual_evidence,
                    ]
                ),
            ),
        )
        for name, stdout in eligible:
            with self.subTest(eligible=name):
                self.assertEqual(
                    classify_provider_balance_failure(self.config, deepseek, stdout),
                    "provider_insufficient_balance",
                )

        excluded = (
            # Wrong endpoint: the same words from another provider.
            (
                "wrong_endpoint",
                json.dumps(
                    {
                        "type": "turn.failed",
                        "error": {
                            "message": (
                                "unexpected status 402 Payment Required: Insufficient Balance"
                            ),
                            "url": "https://api.example-provider.com/v1/responses",
                        },
                    }
                ),
            ),
            # Generic payment failure without insufficient-balance evidence.
            (
                "generic_402",
                self._deepseek_balance_envelope(
                    message="unexpected status 402 Payment Required"
                ),
            ),
            # Balance wording without the verified payment status.
            (
                "no_status",
                self._deepseek_balance_envelope(message="Insufficient Balance"),
            ),
            # Authentication, rate, context, and transport failures keep their
            # own handling and never choose the balance fallback.
            (
                "auth",
                self._deepseek_balance_envelope(
                    message="unexpected status 401 Unauthorized: invalid api key"
                ),
            ),
            (
                "auth_shaped_as_payment",
                self._deepseek_balance_envelope(
                    message=(
                        "unexpected status 402 Payment Required: Insufficient Balance "
                        "after authentication was rejected"
                    )
                ),
            ),
            (
                "rate_limit",
                self._deepseek_balance_envelope(
                    message="unexpected status 429 Too Many Requests: rate limit"
                ),
            ),
            (
                "context_window",
                self._deepseek_balance_envelope(
                    message="prompt is too long: 250000 tokens > 128000"
                ),
            ),
            (
                "network",
                self._deepseek_balance_envelope(
                    message="request failed: ECONNREFUSED while contacting the provider"
                ),
            ),
            # Successful prose, tool output, and agent messages are never read.
            (
                "successful_agent_message",
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": (
                                "I saw unexpected status 402 Payment Required: "
                                f"Insufficient Balance at {endpoint}/responses "
                                "but finished anyway."
                            ),
                        },
                    }
                ),
            ),
            (
                "completed_turn",
                json.dumps(
                    {
                        "type": "turn.completed",
                        "message": (
                            "unexpected status 402 Payment Required: Insufficient "
                            f"Balance, url: {endpoint}/responses"
                        ),
                    }
                ),
            ),
            # Unstructured process text is not an envelope.
            (
                "plain_text",
                "ERROR unexpected status 402 Payment Required: Insufficient Balance, "
                f"url: {endpoint}/responses\n",
            ),
            ("empty", ""),
            ("malformed", "{not json"),
        )
        for name, stdout in excluded:
            with self.subTest(excluded=name):
                self.assertIsNone(
                    classify_provider_balance_failure(self.config, deepseek, stdout)
                )

        # A worker without a configured provider has no trusted balance evidence.
        self.assertIsNone(
            classify_provider_balance_failure(self.config, terra, actual_evidence)
        )
        self.assertIsNone(
            classify_provider_balance_failure(
                self.config, self.config.resolve_worker_binding("opus"), actual_evidence
            )
        )

    def test_each_capacity_exhaustion_starts_exactly_one_approved_continuation(self) -> None:
        cases = (
            (
                "token_quota_exhausted",
                "API Error: insufficient quota for this workspace",
            ),
            (
                "usage_rate_window_exhausted",
                "Claude usage limit reached. Your limit will reset at 3pm.",
            ),
            (
                "context_window_exhausted",
                "prompt is too long: 250000 tokens > 200000 maximum",
            ),
        )
        for expected_category, message in cases:
            with self.subTest(expected_category=expected_category):
                with tempfile.TemporaryDirectory() as temp:
                    controller_calls: list[str] = []
                    request, outcome = self._exhausted_heavy_outcome(
                        Path(temp),
                        envelope=self._claude_envelope(
                            result=message,
                            is_error=True,
                            subtype="error_during_execution",
                        ),
                        controller_calls=controller_calls,
                    )
                    primary = outcome["primary"]
                    continuation = outcome["continuation"]
                    self.assertEqual(primary["status"], "failed")
                    self.assertEqual(primary["route"], "opus")
                    self.assertEqual(primary["failure_category"], expected_category)
                    self.assertTrue(continuation["attempted"])
                    self.assertEqual(continuation["failure_category"], expected_category)
                    self.assertEqual(continuation["route"], "deepseek")
                    self.assertEqual(
                        continuation["route"], self.config.heavy_exhaustion_fallback_route
                    )
                    receipt = continuation["receipt"]
                    self.assertEqual(receipt["status"], "completed")
                    self.assertEqual(validate_receipt(self.config, receipt), [])
                    self.assertEqual(outcome["status"], "completed")
                    self.assertEqual(outcome["effective_result"], "continuation")
                    self.assertEqual(effective_worker_receipt(outcome), receipt)

                    # Exactly one fresh approved controller plan per attempt and
                    # no second continuation after a completed fallback.
                    self.assertEqual(controller_calls, ["opus", "deepseek"])
                    self.assertEqual(receipt["continuation"]["failure_category"], expected_category)
                    self.assertEqual(
                        receipt["continuation"]["prior_receipt_digest"],
                        receipt_digest(primary),
                    )
                    self.assertEqual(
                        receipt["continuation"]["input_baseline"], primary["input_manifest"]
                    )
                    attempts = receipt["continuation"]["attempts"]
                    self.assertEqual(len(attempts), 1)
                    self.assertEqual(attempts[0]["route"], "opus")
                    self.assertEqual(attempts[0]["origin"], continuation_attempt_origin(1, "opus"))
                    self.assertEqual(attempts[0]["failure_category"], expected_category)
                    self.assertEqual(attempts[0]["receipt_digest"], receipt_digest(primary))
                    self.assertEqual(
                        attempts[0]["partial_manifest"], primary["output_manifest"]
                    )
                    self.assertEqual(
                        evaluate_continuation_eligibility(self.config, receipt),
                        (None, "worker_did_not_fail"),
                    )
                    self.assertEqual(len(outcome["continuations"]), 1)

                    # The failed stage, its partial output, and the original
                    # source tree are all preserved.
                    partial = Path(primary["stage_root"]) / "outputs" / "generated" / "result.txt"
                    self.assertEqual(partial.read_text(encoding="utf-8"), "partial heavy progress\n")
                    reference = (
                        Path(receipt["stage_root"])
                        / "reference"
                        / "partial-outputs"
                        / continuation_attempt_origin(1, "opus")
                        / "generated"
                        / "result.txt"
                    )
                    self.assertEqual(
                        reference.read_text(encoding="utf-8"), "partial heavy progress\n"
                    )
                    self.assertEqual(
                        continuation["preserved_partial_manifest"], primary["output_manifest"]
                    )
                    self.assertEqual(receipt["source_root"], primary["source_root"])
                    self.assertEqual(receipt["source_root"], str(request.source_root.resolve()))
                    self.assertNotEqual(receipt["stage_root"], primary["stage_root"])
                    self.assertFalse((request.source_root / "generated" / "result.txt").exists())

                    labelled = json.loads(
                        (
                            Path(receipt["stage_root"])
                            / "workspace"
                            / "control"
                            / "continuation.json"
                        ).read_text(encoding="utf-8")
                    )
                    self.assertEqual(
                        labelled["label"], "prior_partial_outputs_are_unverified_reference_only"
                    )
                    brief = (
                        Path(receipt["stage_root"]) / "workspace" / "control" / "brief.txt"
                    ).read_text(encoding="utf-8")
                    self.assertIn("AUTOMATIC CONTINUATION CONTEXT", brief)
                    self.assertIn("reference only", brief)
                    self.assertIn(request.brief.strip(), brief)

    def test_explicit_deepseek_balance_failure_continues_once_on_terra(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = self._request(root, route="deepseek")
            controller_calls: list[str] = []
            outcome = run_staged_worker_with_continuation(
                self.config,
                request,
                runner=self._codex_provider_runner(
                    self._codex_failure_runner(stdout=self._deepseek_balance_envelope()),
                    self._codex_worker_runner(
                        files={"generated/result.txt": "terra completed result\n"},
                        report=dict(COMPLETION_REPORT),
                    ),
                ),
                controller_runner=self._counting_controller_runner(controller_calls),
                environment=self._test_environment(),
            )
            primary = outcome["primary"]
            self.assertEqual(primary["route"], "deepseek")
            self.assertEqual(primary["status"], "failed")
            self.assertEqual(primary["failure_category"], "provider_insufficient_balance")
            self.assertEqual(validate_receipt(self.config, primary), [])

            record = outcome["continuation"]
            self.assertTrue(record["attempted"])
            self.assertEqual(record["route"], "terra")
            self.assertEqual(record["failure_category"], "provider_insufficient_balance")
            receipt = record["receipt"]
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(receipt["route"], "terra")
            self.assertEqual(validate_receipt(self.config, receipt), [])
            self.assertEqual(outcome["status"], "completed")
            self.assertEqual(outcome["effective_result"], "continuation")
            self.assertEqual(effective_worker_receipt(outcome), receipt)

            # One fresh approved controller plan per attempt, and Terra ends it.
            self.assertEqual(controller_calls, ["deepseek", "terra"])
            self.assertEqual(len(outcome["continuations"]), 1)
            self.assertEqual(
                [attempt["route"] for attempt in receipt["continuation"]["attempts"]],
                ["deepseek"],
            )
            self.assertEqual(receipt["continuation"]["prior_route"], "deepseek")
            self.assertEqual(
                receipt["continuation"]["failure_category"], "provider_insufficient_balance"
            )
            self.assertEqual(
                evaluate_continuation_eligibility(self.config, receipt),
                (None, "route_has_no_configured_continuation_edge"),
            )

            # The failed DeepSeek stage is preserved and nothing reached source.
            self.assertTrue(
                (Path(primary["stage_root"]) / "workspace" / "control" / "brief.txt").is_file()
            )
            self.assertNotEqual(receipt["stage_root"], primary["stage_root"])
            self.assertEqual(receipt["source_root"], str(request.source_root.resolve()))
            self.assertFalse((request.source_root / "generated" / "result.txt").exists())

    def test_other_deepseek_failures_never_choose_the_balance_fallback(self) -> None:
        cases = (
            ("generic_402", self._deepseek_balance_envelope(
                message="unexpected status 402 Payment Required"
            )),
            ("auth", self._deepseek_balance_envelope(
                message="unexpected status 401 Unauthorized: invalid api key"
            )),
            ("network", self._deepseek_balance_envelope(
                message="request failed: ECONNREFUSED while contacting the provider"
            )),
            ("context", self._deepseek_balance_envelope(
                message="prompt is too long: 250000 tokens > 128000"
            )),
            ("wrong_endpoint", json.dumps({
                "type": "turn.failed",
                "error": {
                    "message": "unexpected status 402 Payment Required: Insufficient Balance",
                    "url": "https://api.example-provider.com/v1/responses",
                },
            })),
        )
        for name, stdout in cases:
            with self.subTest(case=name):
                with tempfile.TemporaryDirectory() as temp:
                    request = self._request(Path(temp), route="deepseek")
                    outcome = run_staged_worker_with_continuation(
                        self.config,
                        request,
                        runner=self._codex_provider_runner(
                            self._codex_failure_runner(stdout=stdout),
                            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                                AssertionError("an excluded failure must not use Terra")
                            ),
                        ),
                        controller_runner=self._approved_controller_runner,
                        environment=self._test_environment(),
                    )
                    primary = outcome["primary"]
                    self.assertEqual(primary["status"], "failed")
                    self.assertEqual(primary["failure_category"], "generic_failure")
                    self.assertFalse(outcome["continuation"]["attempted"])
                    self.assertIsNone(outcome["continuation"]["receipt"])
                    self.assertEqual(outcome["effective_result"], "primary")
                    self.assertEqual(effective_worker_receipt(outcome), primary)
                    self.assertEqual(validate_receipt(self.config, primary), [])

    def test_exhaustion_then_balance_failure_completes_on_terra_and_applies_to_source(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller_calls: list[str] = []
            request, outcome = self._balance_chain_outcome(
                root, controller_calls=controller_calls
            )
            primary = outcome["primary"]
            balance = outcome["continuations"][0]
            final = outcome["continuations"][1]
            balance_receipt = balance["receipt"]
            worker = final["receipt"]

            self.assertEqual(controller_calls, ["opus", "deepseek", "terra"])
            self.assertEqual([record["route"] for record in outcome["continuations"]],
                             ["deepseek", "terra"])
            self.assertEqual(primary["failure_category"], "usage_rate_window_exhausted")
            self.assertEqual(
                balance_receipt["failure_category"], "provider_insufficient_balance"
            )
            self.assertEqual(balance_receipt["output_manifest"], {})
            self.assertEqual(worker["status"], "completed")
            self.assertEqual(worker["route"], "terra")
            self.assertEqual(validate_receipt(self.config, worker), [])
            self.assertEqual(outcome["status"], "completed")
            self.assertEqual(effective_worker_receipt(outcome), worker)

            # Cumulative lineage: both earlier attempts stay recorded, each with
            # its own origin label and hashes.
            attempts = worker["continuation"]["attempts"]
            self.assertEqual([attempt["route"] for attempt in attempts], ["opus", "deepseek"])
            self.assertEqual(
                [attempt["origin"] for attempt in attempts],
                [continuation_attempt_origin(1, "opus"), continuation_attempt_origin(2, "deepseek")],
            )
            self.assertEqual(attempts[0]["receipt_digest"], receipt_digest(primary))
            self.assertEqual(attempts[1]["receipt_digest"], receipt_digest(balance_receipt))
            self.assertEqual(attempts[0]["partial_manifest"], primary["output_manifest"])
            self.assertEqual(attempts[1]["partial_manifest"], {})
            self.assertEqual(worker["continuation"]["prior_route"], "deepseek")
            self.assertEqual(
                worker["continuation"]["failure_category"], "provider_insufficient_balance"
            )
            # Every attempt, including programmatic continuation hops, leaves
            # a standalone receipt snapshot that hashes to its recorded value.
            for receipt in (primary, balance_receipt, worker):
                snapshot = json.loads(
                    (
                        Path(receipt["stage_root"])
                        / "workspace"
                        / "control"
                        / "worker-receipt.json"
                    ).read_text(encoding="utf-8")
                )
                self.assertEqual(receipt_digest(snapshot), receipt_digest(receipt))

            # The Opus partial survives the DeepSeek attempt that produced no
            # output of its own, with unambiguous origin in both later stages.
            for stage in (Path(balance_receipt["stage_root"]), Path(worker["stage_root"])):
                carried = (
                    stage
                    / "reference"
                    / "partial-outputs"
                    / continuation_attempt_origin(1, "opus")
                    / "generated"
                    / "result.txt"
                )
                self.assertEqual(
                    carried.read_text(encoding="utf-8"), "partial heavy progress\n"
                )
            self.assertFalse(
                (
                    Path(worker["stage_root"])
                    / "reference"
                    / "partial-outputs"
                    / continuation_attempt_origin(2, "deepseek")
                ).exists()
            )
            self.assertEqual(
                (Path(primary["stage_root"]) / "outputs" / "generated" / "result.txt").read_text(
                    encoding="utf-8"
                ),
                "partial heavy progress\n",
            )
            labelled = json.loads(
                (
                    Path(worker["stage_root"]) / "workspace" / "control" / "continuation.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                labelled["label"], "prior_partial_outputs_are_unverified_reference_only"
            )
            self.assertEqual(len(labelled["attempts"]), 2)
            brief = (
                Path(worker["stage_root"]) / "workspace" / "control" / "brief.txt"
            ).read_text(encoding="utf-8")
            self.assertIn(request.brief.strip(), brief)
            self.assertEqual(brief.count("AUTOMATIC CONTINUATION CONTEXT"), 1)
            self.assertIn(continuation_attempt_origin(1, "opus"), brief)
            self.assertIn(continuation_attempt_origin(2, "deepseek"), brief)

            # The final executed result is reviewed and applied to the ORIGINAL
            # source root, never to a staged snapshot.
            effective_request = request_from_receipt(self.config, worker)
            self.assertEqual(effective_request.route, "terra")
            self.assertEqual(
                str(effective_request.source_root), str(request.source_root.resolve())
            )
            self.assertEqual(effective_request.inputs, ("notes.txt",))
            self.assertEqual(effective_request.approved_outputs, ("generated/result.txt",))
            self.assertIsNotNone(effective_request.continuation)

            def reviewer_runner(
                command: tuple[str, ...], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                package_root = Path(str(kwargs["cwd"]))
                evidence = json.loads(
                    (package_root / "review-evidence.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    evidence["continuation"]["prior_receipt_digest"],
                    receipt_digest(balance_receipt),
                )
                self.assertEqual(len(evidence["continuation"]["attempts"]), 2)
                outcome_object = {
                    "status": "approved",
                    "reviewed_worker_digest": receipt_digest(worker),
                    "reviewed_output_manifest": worker["output_manifest"],
                    "reviewed_brief_digest": worker["brief_digest"],
                    "reviewed_input_manifest": worker["input_manifest"],
                    "reviewed_workflow_selection": worker["workflow_selection"],
                    "reviewed_controller_receipt_digest": worker["controller_receipt_digest"],
                    "reviewed_controller_plan_digest": worker["controller_plan_digest"],
                    "fresh_process": True,
                    "evidence": ["independent review of the final continuation candidate"],
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(outcome_object), "")

            def reviewer_must_not_start(*_args: object, **_kwargs: object) -> object:
                raise AssertionError("ancestor integrity failure must block reviewer launch")

            opus_control = Path(primary["stage_root"]) / "workspace" / "control"
            opus_receipt = opus_control / "worker-receipt.json"
            original_opus_receipt = opus_receipt.read_bytes()
            opus_receipt.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(BoundaryError):
                run_independent_reviewer(
                    self.config,
                    host="codex",
                    worker_receipt=worker,
                    review_root=root / "tampered-opus-receipt-review",
                    runner=reviewer_must_not_start,
                    environment={"PATH": "/usr/bin"},
                    workflow_selection=worker["workflow_selection"],
                )
            opus_receipt.write_bytes(original_opus_receipt)

            opus_receipt.unlink()
            with self.assertRaises(BoundaryError):
                run_independent_reviewer(
                    self.config,
                    host="codex",
                    worker_receipt=worker,
                    review_root=root / "missing-opus-receipt-review",
                    runner=reviewer_must_not_start,
                    environment={"PATH": "/usr/bin"},
                    workflow_selection=worker["workflow_selection"],
                )
            opus_receipt.write_bytes(original_opus_receipt)

            opus_plan = opus_control / "controller-plan.json"
            original_opus_plan = opus_plan.read_bytes()
            opus_plan.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(BoundaryError):
                run_independent_reviewer(
                    self.config,
                    host="codex",
                    worker_receipt=worker,
                    review_root=root / "tampered-opus-plan-review",
                    runner=reviewer_must_not_start,
                    environment={"PATH": "/usr/bin"},
                    workflow_selection=worker["workflow_selection"],
                )
            opus_plan.write_bytes(original_opus_plan)

            opus_request = opus_control / "controller-request.json"
            original_opus_request = opus_request.read_bytes()
            opus_request.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(BoundaryError):
                run_independent_reviewer(
                    self.config,
                    host="codex",
                    worker_receipt=worker,
                    review_root=root / "tampered-opus-request-review",
                    runner=reviewer_must_not_start,
                    environment={"PATH": "/usr/bin"},
                    workflow_selection=worker["workflow_selection"],
                )
            opus_request.write_bytes(original_opus_request)

            review = run_independent_reviewer(
                self.config,
                host="codex",
                worker_receipt=worker,
                review_root=root / "chain-review",
                runner=reviewer_runner,
                environment={"PATH": "/usr/bin"},
                workflow_selection=worker["workflow_selection"],
            )
            self.assertEqual(review["status"], "approved")
            self.assertEqual(validate_receipt(self.config, review), [])
            # The same original-hop checks run again at the final apply gate.
            opus_plan.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(BoundaryError):
                apply_reviewed_outputs(self.config, effective_request, worker, review)
            opus_plan.write_bytes(original_opus_plan)
            opus_receipt.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(BoundaryError):
                apply_reviewed_outputs(self.config, effective_request, worker, review)
            opus_receipt.write_bytes(original_opus_receipt)
            apply_reviewed_outputs(self.config, effective_request, worker, review)
            self.assertEqual(
                (request.source_root / "generated" / "result.txt").read_text(encoding="utf-8"),
                "terra completed result\n",
            )

    def test_tampered_earlier_attempt_blocks_a_later_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request, outcome = self._balance_chain_outcome(root)
            primary = outcome["primary"]
            balance_receipt = outcome["continuations"][0]["receipt"]
            self.assertEqual(
                evaluate_continuation_eligibility(self.config, balance_receipt),
                ("provider_insufficient_balance", "eligible"),
            )

            partial = Path(primary["stage_root"]) / "outputs" / "generated" / "result.txt"
            original_partial = partial.read_text(encoding="utf-8")
            partial.write_text("tampered earlier partial\n", encoding="utf-8")
            category, reason = evaluate_continuation_eligibility(self.config, balance_receipt)
            self.assertIsNone(category)
            self.assertIn("earlier attempt partial output changed", reason)
            with self.assertRaises(BoundaryError):
                plan_continuation_request(
                    self.config, request_from_receipt(self.config, balance_receipt), balance_receipt
                )
            partial.write_text(original_partial, encoding="utf-8")

            earlier_brief = (
                Path(primary["stage_root"]) / "workspace" / "control" / "brief.txt"
            )
            original_brief = earlier_brief.read_text(encoding="utf-8")
            earlier_brief.write_text("tampered earlier brief", encoding="utf-8")
            category, reason = evaluate_continuation_eligibility(self.config, balance_receipt)
            self.assertIsNone(category)
            self.assertIn("earlier attempt brief changed", reason)
            earlier_brief.write_text(original_brief, encoding="utf-8")

            earlier_input = Path(primary["stage_root"]) / "inputs" / "notes.txt"
            original_input = earlier_input.read_text(encoding="utf-8")
            earlier_input.write_text("tampered earlier staged input\n", encoding="utf-8")
            category, reason = evaluate_continuation_eligibility(self.config, balance_receipt)
            self.assertIsNone(category)
            self.assertIn("earlier attempt staged inputs changed", reason)
            earlier_input.write_text(original_input, encoding="utf-8")

            # A tampered lineage digest and an already-attempted route both stop
            # the chain even when every staged file still verifies.
            tampered = dict(balance_receipt)
            lineage = json.loads(json.dumps(balance_receipt["continuation"]))
            lineage["attempts"][0]["receipt_digest"] = "3" * 64
            tampered["continuation"] = lineage
            self.assertIsNone(
                evaluate_continuation_eligibility(self.config, tampered)[0]
            )
            repeated = dict(balance_receipt)
            repeated_lineage = json.loads(json.dumps(balance_receipt["continuation"]))
            repeated_lineage["attempts"].append(
                {
                    **repeated_lineage["attempts"][0],
                    "route": "terra",
                    "origin": continuation_attempt_origin(2, "terra"),
                }
            )
            repeated["continuation"] = repeated_lineage
            self.assertIsNone(
                evaluate_continuation_eligibility(self.config, repeated)[0]
            )

    def test_excluded_failures_and_deceptive_prose_never_start_a_continuation(self) -> None:
        deceptive_report = dict(COMPLETION_REPORT)
        deceptive_report["limitations"] = [
            "I nearly hit the usage limit and context window quota while working"
        ]
        cases = (
            (
                "auth_failure",
                self._claude_envelope(
                    result="Authentication failed; please run /login. Usage limit untouched.",
                    is_error=True,
                    subtype="error_during_execution",
                ),
                1,
                "failed",
                "generic_failure",
            ),
            (
                "permission_denied",
                self._claude_envelope(
                    result="Permission denied: Write(/outside/file) is not allowed",
                    is_error=True,
                    subtype="error_during_execution",
                ),
                1,
                "failed",
                "generic_failure",
            ),
            (
                "max_turns",
                self._claude_envelope(
                    result="usage limit reached",
                    is_error=True,
                    subtype="error_max_turns",
                ),
                1,
                "failed",
                "generic_failure",
            ),
            (
                "output_truncation",
                self._claude_envelope(
                    result="context window exceeded for this response",
                    is_error=True,
                    subtype="error_max_tokens",
                ),
                1,
                "failed",
                "generic_failure",
            ),
            (
                "malformed_completion_with_quota_prose",
                self._claude_envelope(
                    result="I ran out of quota and hit the usage limit, so here is prose only."
                ),
                0,
                "failed",
                "generic_failure",
            ),
            (
                "successful_report_mentioning_quota",
                self._claude_envelope(result=json.dumps(deceptive_report)),
                0,
                "completed",
                "none",
            ),
        )
        for name, envelope, returncode, expected_status, expected_category in cases:
            with self.subTest(case=name):
                with tempfile.TemporaryDirectory() as temp:
                    request = self._request(Path(temp), route="opus")
                    outcome = run_staged_worker_with_continuation(
                        self.config,
                        request,
                        runner=self._routed_runner(
                            self._claude_worker_runner(
                                envelope=envelope,
                                files={"generated/result.txt": "staged result\n"},
                                returncode=returncode,
                            ),
                            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                                AssertionError("an excluded failure must not continue")
                            ),
                        ),
                        controller_runner=self._approved_controller_runner,
                        environment=self._test_environment(),
                    )
                    self.assertEqual(outcome["primary"]["status"], expected_status)
                    self.assertEqual(outcome["primary"]["failure_category"], expected_category)
                    self.assertFalse(outcome["continuation"]["attempted"])
                    self.assertIsNone(outcome["continuation"]["receipt"])
                    self.assertIsNone(outcome["continuation"]["failure_category"])
                    self.assertEqual(outcome["effective_result"], "primary")
                    self.assertEqual(validate_receipt(self.config, outcome["primary"]), [])

    def test_launch_failure_and_timeout_stay_generic_and_preserve_partials(self) -> None:
        for name, raises in (
            ("timeout", subprocess.TimeoutExpired("claude", 30)),
            ("launch_failed", OSError("no such executable")),
        ):
            with self.subTest(case=name):
                with tempfile.TemporaryDirectory() as temp:
                    request = self._request(Path(temp), route="opus")
                    outcome = run_staged_worker_with_continuation(
                        self.config,
                        request,
                        runner=self._claude_worker_runner(
                            envelope="",
                            files={"generated/result.txt": "partial heavy progress\n"},
                            raises=raises,
                        ),
                        controller_runner=self._approved_controller_runner,
                        environment=self._test_environment(),
                    )
                    primary = outcome["primary"]
                    self.assertEqual(primary["status"], "failed")
                    self.assertEqual(primary["failure_category"], "generic_failure")
                    self.assertFalse(outcome["continuation"]["attempted"])
                    partial = Path(primary["stage_root"]) / "outputs" / "generated" / "result.txt"
                    self.assertEqual(
                        partial.read_text(encoding="utf-8"), "partial heavy progress\n"
                    )

    def test_tampered_lineage_blocks_a_continuation(self) -> None:
        exhausted = self._claude_envelope(
            result="API Error: insufficient quota for this workspace",
            is_error=True,
            subtype="error_during_execution",
        )

        def failed_primary(root: Path) -> tuple[DispatchRequest, dict[str, object]]:
            request = self._request(root, route="opus")
            receipt = run_staged_worker(
                self.config,
                request,
                runner=self._claude_worker_runner(
                    envelope=exhausted,
                    files={"generated/result.txt": "partial heavy progress\n"},
                    returncode=1,
                ),
                controller_runner=self._approved_controller_runner,
                environment=self._test_environment(),
            )
            self.assertEqual(receipt["failure_category"], "token_quota_exhausted")
            self.assertEqual(
                evaluate_continuation_eligibility(self.config, receipt),
                ("token_quota_exhausted", "eligible"),
            )
            return request, receipt

        with tempfile.TemporaryDirectory() as temp:
            request, receipt = failed_primary(Path(temp))
            (Path(receipt["stage_root"]) / "inputs" / "notes.txt").write_text(
                "tampered staged input\n", encoding="utf-8"
            )
            category, reason = evaluate_continuation_eligibility(self.config, receipt)
            self.assertIsNone(category)
            self.assertEqual(reason, "staged_inputs_changed_after_the_failed_attempt")

        with tempfile.TemporaryDirectory() as temp:
            request, receipt = failed_primary(Path(temp))
            (request.source_root / "notes.txt").write_text("tampered source\n", encoding="utf-8")
            category, reason = evaluate_continuation_eligibility(self.config, receipt)
            self.assertIsNone(category)
            self.assertIn("original source baseline changed", reason)

        with tempfile.TemporaryDirectory() as temp:
            request, receipt = failed_primary(Path(temp))
            plan = Path(receipt["stage_root"]) / "workspace" / "control" / "controller-plan.json"
            plan.write_text("{}\n", encoding="utf-8")
            category, reason = evaluate_continuation_eligibility(self.config, receipt)
            self.assertIsNone(category)
            self.assertIn("controller plan", reason)

        with tempfile.TemporaryDirectory() as temp:
            request, receipt = failed_primary(Path(temp))
            tampered = dict(receipt)
            tampered["failure_category"] = "none"
            self.assertIsNone(evaluate_continuation_eligibility(self.config, tampered)[0])
            tampered = dict(receipt)
            tampered["controller_receipt_digest"] = "0" * 64
            self.assertIsNone(evaluate_continuation_eligibility(self.config, tampered)[0])

        with tempfile.TemporaryDirectory() as temp:
            request, receipt = failed_primary(Path(temp))
            (Path(receipt["stage_root"]) / "outputs" / "generated" / "result.txt").write_text(
                "tampered partial\n", encoding="utf-8"
            )
            with self.assertRaises(BoundaryError):
                plan_continuation_request(self.config, request, receipt)

        with tempfile.TemporaryDirectory() as temp:
            request, receipt = failed_primary(Path(temp))
            continuation_request = plan_continuation_request(self.config, request, receipt)
            assert continuation_request.continuation is not None
            lineage = continuation_request.continuation
            forged_attempt = ContinuationAttempt(
                route=lineage.attempts[-1].route,
                failure_category=lineage.attempts[-1].failure_category,
                stage_root=lineage.attempts[-1].stage_root,
                receipt_digest=lineage.attempts[-1].receipt_digest,
                brief_digest=lineage.attempts[-1].brief_digest,
                origin=lineage.attempts[-1].origin,
                partial_manifest={"generated/result.txt": "1" * 64},
            )
            forged = replace(
                continuation_request,
                stage_root=Path(temp) / "forged-stage",
                continuation=ContinuationContext(
                    failure_category=lineage.failure_category,
                    prior_route=lineage.prior_route,
                    prior_stage_root=lineage.prior_stage_root,
                    prior_receipt_digest=lineage.prior_receipt_digest,
                    prior_brief_digest=lineage.prior_brief_digest,
                    input_baseline=lineage.input_baseline,
                    partial_manifest={"generated/result.txt": "1" * 64},
                    attempts=(forged_attempt,),
                ),
            )
            with self.assertRaises(BoundaryError):
                prepare_stage(forged)

            # A lineage whose attempt list contradicts its own prior_* fields is
            # rejected before any reference material is staged.
            mismatched = replace(
                continuation_request,
                stage_root=Path(temp) / "mismatched-stage",
                continuation=ContinuationContext(
                    failure_category=lineage.failure_category,
                    prior_route=lineage.prior_route,
                    prior_stage_root=lineage.prior_stage_root,
                    prior_receipt_digest=lineage.prior_receipt_digest,
                    prior_brief_digest=lineage.prior_brief_digest,
                    input_baseline=lineage.input_baseline,
                    partial_manifest=lineage.partial_manifest,
                    attempts=(
                        ContinuationAttempt(
                            route=lineage.attempts[-1].route,
                            failure_category=lineage.attempts[-1].failure_category,
                            stage_root=lineage.attempts[-1].stage_root,
                            receipt_digest="2" * 64,
                            brief_digest=lineage.attempts[-1].brief_digest,
                            origin=lineage.attempts[-1].origin,
                            partial_manifest=lineage.attempts[-1].partial_manifest,
                        ),
                    ),
                ),
            )
            with self.assertRaises(BoundaryError):
                prepare_stage(mismatched)

    def test_success_requires_a_structured_completion_report_and_review_before_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = self._request(root)

            def runner(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                final_path = Path(command[command.index("--output-last-message") + 1])
                final_path.parent.mkdir(parents=True, exist_ok=True)
                output = final_path.parents[2] / "outputs" / "generated" / "result.txt"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("staged result\n", encoding="utf-8")
                final_path.write_text(
                    json.dumps(
                        {
                            "status": "completed",
                            "changed_outputs": ["generated/result.txt"],
                            "self_tests": ["python -m unittest"],
                            "limitations": ["no source-tree write was attempted"],
                            "nested_dispatch": "not_attempted",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            receipt = run_staged_worker(
                self.config,
                request,
                runner=runner,
                controller_runner=self._approved_controller_runner,
                environment=self._test_environment(),
            )
            self.assertEqual(receipt["status"], "completed")
            self.assertEqual(validate_receipt(self.config, receipt), [])
            self.assertEqual(receipt["self_tests"], ["python -m unittest"])
            self.assertEqual(receipt["limitations"], ["no source-tree write was attempted"])
            self.assertFalse((request.source_root / "generated" / "result.txt").exists())

            with self.assertRaises(BoundaryError):
                apply_reviewed_outputs(self.config, request, receipt, {})
            with self.assertRaises(BoundaryError):
                run_independent_reviewer(
                    self.config,
                    host="claude",
                    worker_receipt=receipt,
                    review_root=root / "wrong-host-review",
                    runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        AssertionError("mismatched reviewer host must not launch")
                    ),
                    environment={"PATH": "/usr/bin"},
                    workflow_selection=receipt["workflow_selection"],
                )

            reviewer_environment = {
                "PATH": "/usr/bin",
                "HOME": "/synthetic/reviewer-home",
                "CODEX_HOME": "/synthetic/reviewer-codex",
                "OPENAI_API_KEY": "synthetic-reviewer-openai-auth",
                "CODEX_API_KEY": "synthetic-reviewer-codex-auth",
            }

            def reviewer_runner(
                command: tuple[str, ...], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                environment = kwargs["env"]
                self.assertIsInstance(environment, dict)
                for key, value in reviewer_environment.items():
                    self.assertEqual(environment[key], value)
                package_root = Path(str(kwargs["cwd"]))
                self.assertEqual(
                    (package_root / "controller-brief.txt").read_text(encoding="utf-8"),
                    request.brief,
                )
                self.assertEqual(
                    (package_root / "baseline-inputs" / "notes.txt").read_text(encoding="utf-8"),
                    "approved input\n",
                )
                review_evidence = json.loads(
                    (package_root / "review-evidence.json").read_text(encoding="utf-8")
                )
                self.assertEqual(review_evidence["controller_brief_digest"], receipt["brief_digest"])
                self.assertEqual(review_evidence["staged_input_manifest"], receipt["input_manifest"])
                self.assertEqual(review_evidence["controller_receipt_digest"], receipt["controller_receipt_digest"])
                self.assertEqual(review_evidence["controller_plan_digest"], receipt["controller_plan_digest"])
                self.assertIsNone(review_evidence["continuation"])
                self.assertEqual(
                    review_evidence["worker"]["write_boundary"], "explicit_stage_root_only"
                )
                self.assertEqual(
                    review_evidence["worker"]["self_tests"], receipt["self_tests"]
                )
                self.assertEqual(
                    review_evidence["worker"]["limitations"], receipt["limitations"]
                )
                outcome = {
                    "status": "approved",
                    "reviewed_worker_digest": receipt_digest(receipt),
                    "reviewed_output_manifest": receipt["output_manifest"],
                    "reviewed_brief_digest": receipt["brief_digest"],
                    "reviewed_input_manifest": receipt["input_manifest"],
                    "reviewed_workflow_selection": receipt["workflow_selection"],
                    "reviewed_controller_receipt_digest": receipt["controller_receipt_digest"],
                    "reviewed_controller_plan_digest": receipt["controller_plan_digest"],
                    "fresh_process": True,
                    "evidence": ["independent read-only review"],
                }
                codex_event = {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps(outcome),
                    },
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(codex_event) + "\n", "")

            review = run_independent_reviewer(
                self.config,
                host="codex",
                worker_receipt=receipt,
                review_root=root / "review",
                runner=reviewer_runner,
                environment=reviewer_environment,
                workflow_selection=receipt["workflow_selection"],
            )
            self.assertEqual(review["status"], "approved")
            self.assertEqual(validate_receipt(self.config, review), [])
            serialized_review = json.dumps(review)
            self.assertNotIn("synthetic-reviewer-openai-auth", serialized_review)
            self.assertNotIn("synthetic-reviewer-codex-auth", serialized_review)

            (request.source_root / "other.txt").write_text("other input\n", encoding="utf-8")
            for mismatched_request in (
                replace(request, workflow_selection="enabled"),
                replace(request, brief="a different controller brief"),
                replace(request, inputs=("other.txt",)),
                replace(request, controller_host="claude"),
            ):
                with self.subTest(mismatched_request=mismatched_request):
                    with self.assertRaises(BoundaryError):
                        apply_reviewed_outputs(self.config, mismatched_request, receipt, review)

            def outdated_reviewer_runner(
                command: tuple[str, ...], **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    "",
                    "400 "
                    f"{self.config.resolve_host_binding('codex', 'reviewer').model} "
                    "model requires a newer version of Codex; synthetic-secret",
                )

            outdated_review = run_independent_reviewer(
                self.config,
                host="codex",
                worker_receipt=receipt,
                review_root=root / "outdated-reviewer-review",
                runner=outdated_reviewer_runner,
                environment=reviewer_environment,
                workflow_selection=receipt["workflow_selection"],
            )
            self.assertEqual(outdated_review["status"], "failed")
            self.assertEqual(outdated_review["diagnostic"], "model_requires_newer_codex")
            self.assertEqual(validate_receipt(self.config, outdated_review), [])
            self.assertNotIn("synthetic-secret", json.dumps(outdated_review))

            stage_brief = Path(receipt["stage_root"]) / "workspace" / "control" / "brief.txt"
            original_brief = stage_brief.read_text(encoding="utf-8")
            stage_brief.write_text("changed controller brief", encoding="utf-8")
            with self.assertRaises(BoundaryError):
                run_independent_reviewer(
                    self.config,
                    host="codex",
                    worker_receipt=receipt,
                    review_root=root / "changed-brief-review",
                    runner=reviewer_runner,
                    environment=reviewer_environment,
                    workflow_selection=receipt["workflow_selection"],
                )
            stage_brief.write_text(original_brief, encoding="utf-8")
            stage_input = Path(receipt["stage_root"]) / "inputs" / "notes.txt"
            original_input = stage_input.read_text(encoding="utf-8")
            stage_input.write_text("changed staged input", encoding="utf-8")
            with self.assertRaises(BoundaryError):
                run_independent_reviewer(
                    self.config,
                    host="codex",
                    worker_receipt=receipt,
                    review_root=root / "changed-input-review",
                    runner=reviewer_runner,
                    environment=reviewer_environment,
                    workflow_selection=receipt["workflow_selection"],
                )
            stage_input.write_text(original_input, encoding="utf-8")
            fabricated = dict(review)
            fabricated["source"] = "in_process"
            with self.assertRaises(BoundaryError):
                apply_reviewed_outputs(self.config, request, receipt, fabricated)
            staged_output = Path(receipt["stage_root"]) / "outputs" / "generated" / "result.txt"
            staged_output.write_text("changed after review\n", encoding="utf-8")
            with self.assertRaises(BoundaryError):
                apply_reviewed_outputs(self.config, request, receipt, review)
            staged_output.write_text("staged result\n", encoding="utf-8")
            controller_plan = (
                Path(receipt["stage_root"]) / "workspace" / "control" / "controller-plan.json"
            )
            original_plan = controller_plan.read_bytes()
            controller_plan.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(BoundaryError):
                apply_reviewed_outputs(self.config, request, receipt, review)
            controller_plan.write_bytes(original_plan)
            source_input = request.source_root / "notes.txt"
            original_source_input = source_input.read_bytes()
            source_input.write_text("source changed after review\n", encoding="utf-8")
            with self.assertRaises(BoundaryError):
                apply_reviewed_outputs(self.config, request, receipt, review)
            self.assertFalse((request.source_root / "generated").exists())
            source_input.write_bytes(original_source_input)
            apply_reviewed_outputs(self.config, request, receipt, review)
            self.assertEqual(
                (request.source_root / "generated" / "result.txt").read_text(encoding="utf-8"),
                "staged result\n",
            )

    def test_exhaustion_continuation_is_reviewed_and_applied_to_the_original_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request, outcome = self._exhausted_heavy_outcome(
                root,
                envelope=self._claude_envelope(
                    result="Claude usage limit reached; the 5 hour limit is exhausted.",
                    is_error=True,
                    subtype="error_during_execution",
                ),
            )
            primary = outcome["primary"]
            worker = outcome["continuation"]["receipt"]
            self.assertEqual(worker["status"], "completed")

            effective_request = request_from_receipt(self.config, worker)
            self.assertEqual(effective_request.route, "deepseek")
            self.assertEqual(
                str(effective_request.source_root), str(request.source_root.resolve())
            )
            self.assertEqual(effective_request.inputs, ("notes.txt",))
            self.assertEqual(effective_request.approved_outputs, ("generated/result.txt",))
            self.assertIsNotNone(effective_request.continuation)

            def reviewer_runner(
                command: tuple[str, ...], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                package_root = Path(str(kwargs["cwd"]))
                plan = json.loads(
                    (package_root / "controller-plan.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    plan["request_manifest"]["continuation"]["failure_category"],
                    "usage_rate_window_exhausted",
                )
                self.assertEqual(plan["route"], "deepseek")
                evidence = json.loads(
                    (package_root / "review-evidence.json").read_text(encoding="utf-8")
                )
                self.assertEqual(
                    evidence["continuation"]["prior_receipt_digest"], receipt_digest(primary)
                )
                self.assertEqual(
                    (package_root / "baseline-inputs" / "notes.txt").read_text(encoding="utf-8"),
                    "approved input\n",
                )
                outcome_object = {
                    "status": "approved",
                    "reviewed_worker_digest": receipt_digest(worker),
                    "reviewed_output_manifest": worker["output_manifest"],
                    "reviewed_brief_digest": worker["brief_digest"],
                    "reviewed_input_manifest": worker["input_manifest"],
                    "reviewed_workflow_selection": worker["workflow_selection"],
                    "reviewed_controller_receipt_digest": worker["controller_receipt_digest"],
                    "reviewed_controller_plan_digest": worker["controller_plan_digest"],
                    "fresh_process": True,
                    "evidence": ["independent review of the continuation candidate"],
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(outcome_object), "")

            review = run_independent_reviewer(
                self.config,
                host="codex",
                worker_receipt=worker,
                review_root=root / "continuation-review",
                runner=reviewer_runner,
                environment={"PATH": "/usr/bin"},
                workflow_selection=worker["workflow_selection"],
            )
            self.assertEqual(review["status"], "approved")
            self.assertEqual(validate_receipt(self.config, review), [])

            tampered_review = dict(review)
            tampered_review["reviewed_worker_digest"] = "0" * 64
            with self.assertRaises(BoundaryError):
                apply_reviewed_outputs(
                    self.config, effective_request, worker, tampered_review
                )
            with self.assertRaises(BoundaryError):
                apply_reviewed_outputs(
                    self.config,
                    replace(effective_request, continuation=None),
                    worker,
                    review,
                )

            apply_reviewed_outputs(self.config, effective_request, worker, review)
            applied = request.source_root / "generated" / "result.txt"
            self.assertEqual(applied.read_text(encoding="utf-8"), "continued result\n")
            # The result reached the original source root, not a staged snapshot.
            self.assertTrue(applied.is_file())
            self.assertFalse(
                (Path(worker["stage_root"]) / "inputs" / "generated" / "result.txt").exists()
            )
            self.assertEqual(
                (Path(primary["stage_root"]) / "outputs" / "generated" / "result.txt").read_text(
                    encoding="utf-8"
                ),
                "partial heavy progress\n",
            )

    def test_apply_preflights_all_target_paths_before_creating_a_symlinked_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = self._request(root, approved_outputs=("redirect/newdir/result.txt",))
            outside = root / "outside"
            outside.mkdir()
            (request.source_root / "redirect").symlink_to(outside, target_is_directory=True)

            def worker_runner(
                command: tuple[str, ...], **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                final_path = Path(command[command.index("--output-last-message") + 1])
                output = final_path.parents[2] / "outputs" / "redirect" / "newdir" / "result.txt"
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("staged result\n", encoding="utf-8")
                final_path.write_text(
                    json.dumps(
                        {
                            "status": "completed",
                            "changed_outputs": ["redirect/newdir/result.txt"],
                            "self_tests": ["python -m unittest"],
                            "nested_dispatch": "not_attempted",
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            worker = run_staged_worker(
                self.config,
                request,
                runner=worker_runner,
                controller_runner=self._approved_controller_runner,
                environment=self._test_environment(),
            )

            def reviewer_runner(
                command: tuple[str, ...], **_kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                outcome = {
                    "status": "approved",
                    "reviewed_worker_digest": receipt_digest(worker),
                    "reviewed_output_manifest": worker["output_manifest"],
                    "reviewed_brief_digest": worker["brief_digest"],
                    "reviewed_input_manifest": worker["input_manifest"],
                    "reviewed_workflow_selection": worker["workflow_selection"],
                    "reviewed_controller_receipt_digest": worker["controller_receipt_digest"],
                    "reviewed_controller_plan_digest": worker["controller_plan_digest"],
                    "fresh_process": True,
                    "evidence": ["independent review"],
                }
                return subprocess.CompletedProcess(command, 0, json.dumps(outcome), "")

            review = run_independent_reviewer(
                self.config,
                host="codex",
                worker_receipt=worker,
                review_root=root / "review",
                runner=reviewer_runner,
                environment={"PATH": "/usr/bin"},
                workflow_selection=worker["workflow_selection"],
            )
            with self.assertRaises(BoundaryError):
                apply_reviewed_outputs(self.config, request, worker, review)
            self.assertFalse((outside / "newdir").exists())

    def test_zero_exit_without_completion_report_and_timeout_never_claim_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp))

            def zero_exit(command: tuple[str, ...], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(command, 0, "", "")

            incomplete = run_staged_worker(
                self.config,
                request,
                runner=zero_exit,
                controller_runner=self._approved_controller_runner,
                environment=self._test_environment(),
            )
            self.assertEqual(incomplete["status"], "failed")
            self.assertIn("missing_completion_report", incomplete["errors"])

        with tempfile.TemporaryDirectory() as temp:
            request = self._request(Path(temp))

            def timeout(command: tuple[str, ...], **_kwargs: object) -> object:
                raise subprocess.TimeoutExpired(command, 30)

            timed_out = run_staged_worker(
                self.config,
                request,
                runner=timeout,
                controller_runner=self._approved_controller_runner,
                environment=self._test_environment(),
            )
            self.assertEqual(timed_out["status"], "failed")
            self.assertIn("timeout", timed_out["errors"])
            self.assertEqual(timed_out["child_exit_code"], 124)


if __name__ == "__main__":
    unittest.main()
