"""Configuration, routing, staging, launch, and receipt primitives.

This module deliberately keeps the enforcement boundary small and inspectable.
The Codex child receives a fresh, explicit filesystem permission profile whose
only write permission is its resolved staging root.  It does *not* claim that
the ``:root = read`` capability is a confidentiality boundary, nor does it
pretend to enforce an arbitrary file allow-list before the child starts.  The
allow-list is enforced at reviewed application time: workers write staged
outputs and only those reviewed outputs may reach the source tree.

The Claude worker boundary is a different mechanism and is described separately
everywhere it appears.  It is not an OS sandbox profile: the Claude child gets a
restricted file-tool set (no shell, no agents, no MCP) plus exact permission
rules that allow Write/Edit only on the approved output paths and confine
Read/Glob/Grep to the stage.  Both boundaries end at the same reviewed
application gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tomllib
import fcntl
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


class DelegationError(ValueError):
    """Base error for a rejected unified-delegation operation."""


class ConfigError(DelegationError):
    """The single source configuration is malformed or inconsistent."""


class RoutingInputError(DelegationError):
    """Material routing facts are missing or contradictory."""


class BoundaryError(DelegationError):
    """A staging, review, or application boundary would be unsafe."""


WORKFLOW_SELECTIONS = {"enabled", "declined"}
REVIEWER_DIAGNOSTICS = {
    "not_started",
    "none",
    "reviewer_timeout",
    "reviewer_launch_failed",
    "model_requires_newer_codex",
    "reviewer_exit_nonzero",
    "invalid_reviewer_outcome",
}
CONTROLLER_DIAGNOSTICS = {
    "not_started",
    "none",
    "controller_timeout",
    "controller_launch_failed",
    "model_requires_newer_codex",
    "controller_exit_nonzero",
    "invalid_controller_outcome",
}

# Each worker launcher has its own enforced write boundary. Codex uses the OS
# permission profile for the whole stage root; Claude uses restricted file tools
# with exact approved-output Write/Edit rules. Receipts record which one applied.
WORKER_BOUNDARIES = {
    "codex": ("delegation_worker", "explicit_stage_root_only"),
    "claude": ("claude_restricted_file_tools", "approved_output_paths_only"),
}

CODEX_ENVIRONMENT_KEYS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TERM",
    "HOME",
    "CODEX_HOME",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
)
# A Claude worker authenticates through the standard existing Claude Code login
# under HOME. HOME is never emptied, no new credential file is discovered, and
# Codex/OpenAI provider variables are not forwarded to it. The ordinary OS
# session keys after HOME are required as well: a controlled read-only auth
# diagnostic reported logged-out with PATH/LANG/LC_ALL/TERM/HOME alone and
# reported the existing first-party login with these standard keys added. No
# CLAUDE*/ANTHROPIC* variable exists in the parent and none is invented here.
CLAUDE_ENVIRONMENT_KEYS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TERM",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TMPDIR",
    "SECURITYSESSIONID",
    "__CF_USER_TEXT_ENCODING",
)

CAPACITY_FAILURE_CATEGORIES = {
    "token_quota_exhausted",
    "usage_rate_window_exhausted",
    "context_window_exhausted",
}
# A verified configured-provider insufficient-balance failure is a separate
# trigger with its own evidence rules; it is never inferred from capacity words.
PROVIDER_BALANCE_FAILURE_CATEGORY = "provider_insufficient_balance"
CONTINUATION_FAILURE_CATEGORIES = {
    *CAPACITY_FAILURE_CATEGORIES,
    PROVIDER_BALANCE_FAILURE_CATEGORY,
}
FAILURE_CATEGORIES = {"none", "generic_failure", *CONTINUATION_FAILURE_CATEGORIES}

# A capacity classification is read only from a genuine top-level CLI or
# provider failure envelope, and only from the envelope fields below. Successful
# worker prose, tool output, staged input files, and prompts are never scanned
# for these words, so a report that merely talks about quotas cannot trigger a
# continuation.
CAPACITY_ENVELOPE_FIELDS = ("subtype", "message", "result")
# Claude CLI failure envelopes can additionally carry a top-level ``errors``
# list. Only these direct string fields of each entry are trusted: nested
# tool/event payloads are deliberately not traversed.
CAPACITY_ERROR_ENTRY_FIELDS = ("message", "error", "detail", "type", "code")
CAPACITY_EXCLUDED_SUBTYPES = ("error_max_turns", "error_max_tokens")
CAPACITY_EXCLUSION_MARKERS = (
    "authentication",
    "unauthenticated",
    "unauthorized",
    "api key",
    "api_key",
    "oauth",
    "/login",
    "permission",
    "forbidden",
    "not allowed",
    "denied",
    "enotfound",
    "econnrefused",
    "etimedout",
    "network",
    "socket",
    "dns",
    "failed to launch",
    "command not found",
    "timed out",
    "timeout",
    "max turns",
    "max_turns",
    "max tokens",
    "max_tokens",
    "truncat",
    "malformed",
)
CAPACITY_FAILURE_MARKERS = (
    (
        "context_window_exhausted",
        (
            "context window",
            "context length",
            "context_length_exceeded",
            "prompt is too long",
            "input length exceeds",
            "too many tokens in",
        ),
    ),
    (
        "token_quota_exhausted",
        (
            "insufficient quota",
            "quota exhausted",
            "quota exceeded",
            "out of quota",
            "insufficient credit",
            "credit balance exhausted",
            "out of tokens",
        ),
    ),
    (
        "usage_rate_window_exhausted",
        (
            "usage limit reached",
            "usage limit",
            "rate limit",
            "rate_limit",
            "too many requests",
            "weekly limit",
            "hour limit",
            "session limit",
            "monthly spend limit",
            "monthly limit",
            "you've hit your limit",
            "you have hit your limit",
        ),
    ),
)

# An insufficient-balance classification is read only from a top-level Codex
# turn-failure or provider error envelope for the *configured* provider
# endpoint. Streamed item/agent-message events, worker prose, tool output, and
# process stderr are never scanned, so nothing a child writes can trigger the
# balance transition.
CODEX_FAILURE_ENVELOPE_TYPES = frozenset({"turn.failed", "error", "thread.error"})
BALANCE_ENVELOPE_STRING_FIELDS = ("message", "result", "url", "detail")
BALANCE_ENVELOPE_CODE_FIELDS = ("code", "type")
BALANCE_ENVELOPE_STATUS_FIELDS = ("status", "status_code", "http_status")
BALANCE_PAYMENT_REQUIRED_STATUS = "402"
BALANCE_INSUFFICIENT_MARKERS = ("insufficient balance", "insufficient_balance")
BALANCE_STRUCTURED_CODES = ("insufficient_balance", "insufficient-balance")
# Authentication, permission, transport, and capacity failures keep their own
# categories; a generic or malformed payment failure is not an authorized
# balance transition either, because the insufficient-balance evidence above is
# also required.
BALANCE_EXCLUSION_MARKERS = (
    "authentication",
    "unauthenticated",
    "unauthorized",
    "invalid api key",
    "api key",
    "api_key",
    "oauth",
    "/login",
    "permission",
    "forbidden",
    "not allowed",
    "denied",
    "rate limit",
    "rate_limit",
    "too many requests",
    "quota",
    "context window",
    "context length",
    "context_length_exceeded",
    "prompt is too long",
    "enotfound",
    "econnrefused",
    "etimedout",
    "network",
    "socket",
    "dns",
    "timed out",
    "timeout",
)


def _workflow_selection_context(selection: Any) -> str:
    if selection not in WORKFLOW_SELECTIONS:
        raise BoundaryError(
            "workflow_selection must be explicitly enabled or declined for this session"
        )
    return (
        "Controller context: this delegated child belongs to a parent session whose "
        f"mandatory project-working-loop selection is already complete: {selection}. "
        "Do not ask the user to select project-working-loop again. This inherited "
        "workflow-selection state grants no other approval or authority."
    )


@dataclass(frozen=True)
class Binding:
    name: str
    model: str
    effort: str
    launcher: str
    sandbox: str
    provider: str | None = None


@dataclass(frozen=True)
class RouteDecision:
    route: str
    dispatch: bool
    reason: str


@dataclass(frozen=True)
class ContinuationAttempt:
    """One earlier failed attempt preserved in a cumulative lineage.

    ``origin`` is the labelled reference sub-directory that holds this attempt's
    verified partial candidate files, so material from two different providers
    can never be confused for one another.
    """

    route: str
    failure_category: str
    stage_root: str
    receipt_digest: str
    brief_digest: str
    origin: str
    partial_manifest: Mapping[str, str]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "route": str(self.route),
            "failure_category": str(self.failure_category),
            "stage_root": str(self.stage_root),
            "receipt_digest": str(self.receipt_digest),
            "brief_digest": str(self.brief_digest),
            "origin": str(self.origin),
            "partial_manifest": {
                str(path): str(digest) for path, digest in dict(self.partial_manifest).items()
            },
        }

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, Any]) -> "ContinuationAttempt":
        return cls(
            route=str(manifest["route"]),
            failure_category=str(manifest["failure_category"]),
            stage_root=str(manifest["stage_root"]),
            receipt_digest=str(manifest["receipt_digest"]),
            brief_digest=str(manifest["brief_digest"]),
            origin=str(manifest["origin"]),
            partial_manifest=dict(manifest["partial_manifest"]),
        )


def continuation_attempt_origin(index: int, route: str) -> str:
    """Name the reference directory for the index-th attempt in a lineage."""

    return f"{index:02d}-{route}"


@dataclass(frozen=True)
class ContinuationContext:
    """Hash-bound cumulative lineage for an automatic continuation chain.

    It records why each earlier worker stopped, which staged materials those
    attempts were built from, and the exact partial candidate files carried
    forward as *reference only*.  It never marks partial work complete.  The
    ``prior_*`` fields describe the immediately previous attempt; ``attempts``
    keeps every earlier attempt in order so a later transition can revalidate
    all of them rather than only the last one.
    """

    failure_category: str
    prior_route: str
    prior_stage_root: str
    prior_receipt_digest: str
    prior_brief_digest: str
    input_baseline: Mapping[str, str]
    partial_manifest: Mapping[str, str]
    attempts: tuple[ContinuationAttempt, ...]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "failure_category": str(self.failure_category),
            "prior_route": str(self.prior_route),
            "prior_stage_root": str(self.prior_stage_root),
            "prior_receipt_digest": str(self.prior_receipt_digest),
            "prior_brief_digest": str(self.prior_brief_digest),
            "input_baseline": {
                str(path): str(digest) for path, digest in dict(self.input_baseline).items()
            },
            "partial_manifest": {
                str(path): str(digest) for path, digest in dict(self.partial_manifest).items()
            },
            "attempts": [attempt.to_manifest() for attempt in self.attempts],
        }

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, Any]) -> "ContinuationContext":
        errors = continuation_manifest_errors(manifest)
        if errors:
            raise BoundaryError(f"invalid continuation manifest: {','.join(errors)}")
        return cls(
            failure_category=str(manifest["failure_category"]),
            prior_route=str(manifest["prior_route"]),
            prior_stage_root=str(manifest["prior_stage_root"]),
            prior_receipt_digest=str(manifest["prior_receipt_digest"]),
            prior_brief_digest=str(manifest["prior_brief_digest"]),
            input_baseline=dict(manifest["input_baseline"]),
            partial_manifest=dict(manifest["partial_manifest"]),
            attempts=tuple(
                ContinuationAttempt.from_manifest(attempt)
                for attempt in manifest["attempts"]
            ),
        )


@dataclass(frozen=True)
class DispatchRequest:
    """The controller's bounded request for one staged worker.

    ``inputs`` and ``approved_outputs`` are source-root-relative exact paths.
    They are intentionally not globs.  A child never receives the source tree
    as its workspace.  ``continuation`` is set only for an automatic capacity
    continuation; ``source_root`` still names the original source tree, never a
    staged snapshot.
    """

    route: str
    source_root: Path
    stage_root: Path
    inputs: tuple[str, ...]
    approved_outputs: tuple[str, ...]
    brief: str
    timeout_seconds: int
    workflow_selection: str
    controller_host: str
    nested_dispatch_requested: bool = False
    continuation: ContinuationContext | None = None


@dataclass(frozen=True)
class StageLayout:
    root: Path
    workspace: Path
    inputs: Path
    outputs: Path
    control: Path
    source_root: Path
    approved_outputs: tuple[str, ...]
    input_manifest: Mapping[str, str]
    brief_digest: str
    workflow_selection: str


@dataclass(frozen=True)
class ReviewPackage:
    root: Path
    evidence_path: Path
    candidate_outputs: Path
    worker_digest: str
    output_manifest: Mapping[str, str]
    input_manifest: Mapping[str, str]
    brief_digest: str
    workflow_selection: str
    controller_host: str
    controller_receipt_digest: str
    controller_plan_digest: str


@dataclass(frozen=True)
class DispatchLease:
    path: Path

    def release(self) -> None:
        if self.path.is_file() and not self.path.is_symlink():
            self.path.unlink()


@dataclass(frozen=True)
class DelegationConfig:
    path: Path
    config_digest: str
    policy: Mapping[str, Any]
    routing: Mapping[str, str]
    launchers: Mapping[str, Mapping[str, Any]]
    providers: Mapping[str, Mapping[str, Any]]
    permission_profiles: Mapping[str, Mapping[str, Any]]
    bindings: Mapping[str, Binding]
    hosts: Mapping[str, Mapping[str, str]]
    workers: Mapping[str, str]
    compatibility_profiles: Mapping[str, Mapping[str, str]]

    @property
    def heavy_primary_route(self) -> str:
        """The configured automatic heavy-execution worker reference."""

        return str(self.routing["heavy_primary"])

    @property
    def heavy_exhaustion_fallback_route(self) -> str:
        """The configured continuation worker for capacity exhaustion only."""

        return str(self.routing["heavy_exhaustion_fallback"])

    @property
    def insufficient_balance_route(self) -> str:
        """The configured provider route that may report insufficient balance."""

        return str(self.routing["insufficient_balance_route"])

    @property
    def insufficient_balance_fallback_route(self) -> str:
        """The configured terminal worker for a verified balance failure only."""

        return str(self.routing["insufficient_balance_fallback"])

    def continuation_edge(self, route: str) -> tuple[str | None, frozenset[str]]:
        """Return the configured fallback route and its allowed trigger set.

        A route has at most one configured outgoing edge, and the terminal
        fallback route has none, so a runtime chain is finite by construction.
        """

        if route == self.heavy_primary_route:
            return self.heavy_exhaustion_fallback_route, frozenset(CAPACITY_FAILURE_CATEGORIES)
        if route == self.insufficient_balance_route:
            return (
                self.insufficient_balance_fallback_route,
                frozenset({PROVIDER_BALANCE_FAILURE_CATEGORY}),
            )
        return None, frozenset()

    def resolve_host_binding(self, host: str, role: str) -> Binding:
        if role not in {"controller", "reviewer"}:
            raise ConfigError(f"unsupported host role: {role}")
        try:
            reference = self.hosts[host][f"{role}_binding"]
        except KeyError as exc:
            raise ConfigError(f"unsupported host: {host}") from exc
        try:
            return self.bindings[reference]
        except KeyError as exc:
            raise ConfigError(f"unknown host binding: {reference}") from exc

    def resolve_worker_binding(self, route: str) -> Binding:
        try:
            reference = self.workers[route]
            return self.bindings[reference]
        except KeyError as exc:
            raise ConfigError(f"unsupported worker route: {route}") from exc


def _nonblank(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a nonblank string")
    return value.strip()


def _environment_name(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return not any(character.isspace() or character == "=" for character in value)


def _table(raw: Mapping[str, Any], label: str) -> dict[str, Any]:
    value = raw.get(label)
    if not isinstance(value, Mapping):
        raise ConfigError(f"{label} must be a table")
    return dict(value)


def _command(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ConfigError(f"{label}.command must be a nonempty string array")
    return tuple(value)


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{label} must be an integer >= {minimum}")
    return value


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{label} must be boolean")
    return value


def _assert_finite_route_chain(
    edges: Sequence[tuple[str, str]],
    *,
    terminal: str,
) -> None:
    """Reject a continuation graph that is not a finite forward-only chain.

    Self edges, cycles, reverse edges, a route repeated on any path, and any
    outgoing edge from the terminal fallback route are all configuration
    errors, so no runtime chain can loop or grow without bound.
    """

    for source, target in edges:
        if source == target:
            raise ConfigError(f"routing edge {source} cannot fall back to itself")
    if any(source == terminal for source, _ in edges):
        raise ConfigError(f"the terminal fallback route {terminal} must end the chain")

    def walk(route: str, visited: tuple[str, ...]) -> None:
        if route in visited:
            raise ConfigError("routing edges must not repeat a route or form a cycle")
        path = (*visited, route)
        for source, target in edges:
            if source == route:
                walk(target, path)

    for source, _ in edges:
        walk(source, ())


def load_config(path: Path) -> DelegationConfig:
    """Load and validate the single editable delegation configuration."""

    try:
        config_text = Path(path).read_text(encoding="utf-8")
        raw = tomllib.loads(config_text)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot load config: {path}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigError("config root must be a table")

    metadata = _table(raw, "metadata")
    if _integer(metadata.get("schema_version"), "metadata.schema_version", minimum=1) != 1:
        raise ConfigError("unsupported schema version")

    policy = _table(raw, "policy")
    cap = _integer(policy.get("direct_worker_cap"), "policy.direct_worker_cap", minimum=1)
    if cap > 2:
        raise ConfigError("policy.direct_worker_cap cannot exceed 2")
    local_max = _integer(policy.get("local_max_minutes"), "policy.local_max_minutes", minimum=0)
    heavy_near = _integer(policy.get("heavy_near_minutes"), "policy.heavy_near_minutes", minimum=0)
    near_tolerance = _integer(
        policy.get("heavy_near_tolerance_minutes"),
        "policy.heavy_near_tolerance_minutes",
        minimum=0,
    )
    default_timeout = _integer(
        policy.get("default_timeout_seconds"),
        "policy.default_timeout_seconds",
        minimum=1,
    )
    max_timeout = _integer(
        policy.get("max_timeout_seconds"),
        "policy.max_timeout_seconds",
        minimum=1,
    )
    if default_timeout > max_timeout:
        raise ConfigError("default timeout cannot exceed the configured maximum")
    if not _bool(
        policy.get("require_independent_review_before_apply"),
        "policy.require_independent_review_before_apply",
    ):
        raise ConfigError("independent review before apply may not be disabled")
    if not (local_max < heavy_near):
        raise ConfigError("local threshold must be smaller than the heavy-work threshold")
    if near_tolerance >= heavy_near:
        raise ConfigError("near-threshold tolerance must be smaller than the heavy-work threshold")

    raw_launchers = _table(raw, "launchers")
    launchers: dict[str, Mapping[str, Any]] = {}
    for name in ("codex", "claude"):
        launcher = _table(raw_launchers, name)
        _command(launcher.get("command"), f"launchers.{name}")
        launchers[name] = launcher
    for key in ("ignore_user_config", "ephemeral", "skip_git_repo_check", "json_output"):
        _bool(launchers["codex"].get(key), f"launchers.codex.{key}")
    for key in ("no_chrome", "strict_mcp_config", "no_session_persistence", "safe_mode", "restricted"):
        _bool(launchers["claude"].get(key), f"launchers.claude.{key}")
    for key in ("permission_mode", "worker_permission_mode", "empty_mcp_config", "worker_tools"):
        _nonblank(launchers["claude"].get(key), f"launchers.claude.{key}")

    raw_profiles = _table(raw, "permission_profiles")
    permission_profiles: dict[str, Mapping[str, Any]] = {}
    expected_profiles = {"delegation_worker", "delegation_reviewer"}
    if set(raw_profiles) != expected_profiles:
        raise ConfigError("permission profile inventory must be delegation_worker and delegation_reviewer")
    for name, profile_value in raw_profiles.items():
        if not isinstance(profile_value, Mapping):
            raise ConfigError(f"permission profile {name} must be a table")
        profile = dict(profile_value)
        if _nonblank(profile.get("root_access"), f"permission_profiles.{name}.root_access") != "read":
            raise ConfigError(f"{name} must retain root read access declaration")
        if _bool(profile.get("network_enabled"), f"permission_profiles.{name}.network_enabled"):
            raise ConfigError(f"{name} network access must remain disabled")
        if name == "delegation_worker":
            if _nonblank(profile.get("stage_access"), f"permission_profiles.{name}.stage_access") != "write":
                raise ConfigError("delegation_worker must grant only staged write access")
        elif "stage_access" in profile:
            raise ConfigError("delegation_reviewer must not grant a staged write access")
        permission_profiles[name] = profile

    raw_providers = _table(raw, "providers")
    providers: dict[str, Mapping[str, Any]] = {}
    for name, provider_value in raw_providers.items():
        if not isinstance(provider_value, Mapping):
            raise ConfigError(f"provider {name} must be a table")
        provider = dict(provider_value)
        _nonblank(provider.get("name"), f"providers.{name}.name")
        _nonblank(provider.get("base_url"), f"providers.{name}.base_url")
        _nonblank(provider.get("wire_api"), f"providers.{name}.wire_api")
        key_name = _nonblank(provider.get("environment_key"), f"providers.{name}.environment_key")
        if not _environment_name(key_name):
            raise ConfigError(f"providers.{name}.environment_key must be an environment variable name")
        credential_file = provider.get("credential_file")
        if credential_file is not None:
            provider["credential_file"] = _nonblank(
                credential_file,
                f"providers.{name}.credential_file",
            )
        providers[name] = provider

    raw_bindings = _table(raw, "bindings")
    bindings: dict[str, Binding] = {}
    for name, binding_value in raw_bindings.items():
        if not isinstance(binding_value, Mapping):
            raise ConfigError(f"binding {name} must be a table")
        binding = dict(binding_value)
        launcher = _nonblank(binding.get("launcher"), f"bindings.{name}.launcher")
        if launcher not in launchers:
            raise ConfigError(f"binding {name} names an unknown launcher")
        provider_value = binding.get("provider")
        provider = None if provider_value is None else _nonblank(provider_value, f"bindings.{name}.provider")
        if provider is not None and provider not in providers:
            raise ConfigError(f"binding {name} names an unknown provider")
        bindings[name] = Binding(
            name=name,
            model=_nonblank(binding.get("model"), f"bindings.{name}.model"),
            effort=_nonblank(binding.get("effort"), f"bindings.{name}.effort"),
            launcher=launcher,
            sandbox=_nonblank(binding.get("sandbox"), f"bindings.{name}.sandbox"),
            provider=provider,
        )

    raw_hosts = _table(raw, "hosts")
    hosts: dict[str, Mapping[str, str]] = {}
    for host in ("codex", "claude"):
        data = _table(raw_hosts, host)
        controller = _nonblank(data.get("controller_binding"), f"hosts.{host}.controller_binding")
        reviewer = _nonblank(data.get("reviewer_binding"), f"hosts.{host}.reviewer_binding")
        if controller != reviewer:
            raise ConfigError(f"hosts.{host} controller and reviewer must reference the same binding")
        if controller not in bindings:
            raise ConfigError(f"hosts.{host} references an unknown binding")
        binding = bindings[controller]
        if binding.launcher != host or binding.sandbox != "read-only":
            raise ConfigError(f"hosts.{host} highest binding must be read-only and use the {host} launcher")
        hosts[host] = {"controller_binding": controller, "reviewer_binding": reviewer}

    raw_workers = _table(raw, "workers")
    workers: dict[str, str] = {}
    for route in ("terra", "opus", "deepseek"):
        data = _table(raw_workers, route)
        reference = _nonblank(data.get("binding"), f"workers.{route}.binding")
        if reference not in bindings:
            raise ConfigError(f"workers.{route} references an unknown binding")
        binding = bindings[reference]
        expected_launcher = "claude" if route == "opus" else "codex"
        if binding.launcher != expected_launcher or binding.sandbox != "workspace-write":
            raise ConfigError(
                f"workers.{route} must use the {expected_launcher} workspace-write semantic binding"
            )
        if route == "deepseek" and binding.provider is None:
            raise ConfigError("deepseek worker requires a child-specific provider")
        if route in {"terra", "opus"} and binding.provider is not None:
            raise ConfigError(f"{route} worker must not switch a provider")
        workers[route] = reference

    raw_routing = _table(raw, "routing")
    heavy_primary = _nonblank(raw_routing.get("heavy_primary"), "routing.heavy_primary")
    heavy_fallback = _nonblank(
        raw_routing.get("heavy_exhaustion_fallback"),
        "routing.heavy_exhaustion_fallback",
    )
    balance_route = _nonblank(
        raw_routing.get("insufficient_balance_route"),
        "routing.insufficient_balance_route",
    )
    balance_fallback = _nonblank(
        raw_routing.get("insufficient_balance_fallback"),
        "routing.insufficient_balance_fallback",
    )
    for label, reference in (
        ("routing.heavy_primary", heavy_primary),
        ("routing.heavy_exhaustion_fallback", heavy_fallback),
        ("routing.insufficient_balance_route", balance_route),
        ("routing.insufficient_balance_fallback", balance_fallback),
    ):
        if reference not in workers:
            raise ConfigError(f"{label} must reference a configured worker route")
    if heavy_primary == heavy_fallback:
        raise ConfigError("the heavy exhaustion fallback must differ from the heavy primary route")
    if balance_route == balance_fallback:
        raise ConfigError("the insufficient-balance fallback must differ from its own route")
    # Only a route with a configured provider can produce trusted provider
    # balance evidence, and a route may own at most one outgoing edge.
    if bindings[workers[balance_route]].provider is None:
        raise ConfigError("routing.insufficient_balance_route must use a configured provider")
    if balance_route == heavy_primary:
        raise ConfigError("a route may declare at most one continuation edge")
    _assert_finite_route_chain(
        ((heavy_primary, heavy_fallback), (balance_route, balance_fallback)),
        terminal=balance_fallback,
    )
    routing = {
        "heavy_primary": heavy_primary,
        "heavy_exhaustion_fallback": heavy_fallback,
        "insufficient_balance_route": balance_route,
        "insufficient_balance_fallback": balance_fallback,
    }

    raw_compatibility = _table(raw, "compatibility_profiles")
    required_compatibility = {
        "sol-readonly",
        "terra-worker",
        "luna-worker",
        "spark-worker",
        "sol-write-takeover",
    }
    if set(raw_compatibility) != required_compatibility:
        raise ConfigError("compatibility profile names must match the generated inventory")
    compatibility_profiles: dict[str, Mapping[str, str]] = {}
    for name in sorted(required_compatibility):
        data = _table(raw_compatibility, name)
        reference = _nonblank(data.get("binding"), f"compatibility_profiles.{name}.binding")
        if reference not in bindings:
            raise ConfigError(f"compatibility profile {name} references an unknown binding")
        compatibility_profiles[name] = {
            "binding": reference,
            "sandbox": _nonblank(data.get("sandbox"), f"compatibility_profiles.{name}.sandbox"),
            "mode": _nonblank(data.get("mode"), f"compatibility_profiles.{name}.mode"),
        }

    return DelegationConfig(
        path=Path(path).resolve(),
        config_digest=hashlib.sha256(config_text.encode("utf-8")).hexdigest(),
        policy=policy,
        routing=routing,
        launchers=launchers,
        providers=providers,
        permission_profiles=permission_profiles,
        bindings=bindings,
        hosts=hosts,
        workers=workers,
        compatibility_profiles=compatibility_profiles,
    )


def route_workload(
    config: DelegationConfig,
    *,
    minutes_low: int | None,
    minutes_high: int | None,
    confidence: str,
    large_work: bool,
    large_work_reason: str | None,
    self_contained: bool,
) -> RouteDecision:
    """Choose local, Terra, the heavy primary, or clarification without a stopwatch.

    Around-thirty estimates are intentionally qualitative.  An estimate in the
    configurable near-threshold band, a plausible range reaching that band, or
    a documented controller large-work assessment routes to the configured
    heavy primary worker.  Work wholly below that band routes to Terra, and only
    a genuinely unclassifiable uncertain range spanning the threshold asks for
    clarification.  The DeepSeek continuation route is never chosen here: it is
    reached only by explicit user selection or by capacity exhaustion.
    """

    if confidence not in {"high", "plausible", "uncertain"}:
        raise RoutingInputError("confidence must be high, plausible, or uncertain")
    if not isinstance(large_work, bool) or not isinstance(self_contained, bool):
        raise RoutingInputError("large_work and self_contained must be boolean")
    if (minutes_low is None) != (minutes_high is None):
        raise RoutingInputError("minutes_low and minutes_high must be supplied together")
    if minutes_low is not None:
        if isinstance(minutes_low, bool) or isinstance(minutes_high, bool):
            raise RoutingInputError("minute estimates must be integers")
        if not isinstance(minutes_low, int) or not isinstance(minutes_high, int):
            raise RoutingInputError("minute estimates must be integers")
        if minutes_low < 0 or minutes_high < minutes_low:
            raise RoutingInputError("minute estimate range is invalid")

    heavy = config.heavy_primary_route

    if large_work:
        if not isinstance(large_work_reason, str) or not large_work_reason.strip():
            raise RoutingInputError("a controller large-work assessment needs a reason")
        return RouteDecision(heavy, True, "documented controller large-work assessment")

    if minutes_low is None or minutes_high is None:
        raise RoutingInputError("an estimate or documented large-work assessment is required")

    local_max = int(config.policy["local_max_minutes"])
    near = int(config.policy["heavy_near_minutes"])
    tolerance = int(config.policy["heavy_near_tolerance_minutes"])
    near_low = near - tolerance

    if self_contained and minutes_high <= local_max:
        return RouteDecision("local", False, "bounded self-contained work stays in the current flow")
    if minutes_high < near_low:
        return RouteDecision("terra", True, "standard below-threshold execution estimate")
    if minutes_low >= near_low:
        return RouteDecision(heavy, True, "near-or-over-threshold execution estimate")
    if confidence in {"high", "plausible"}:
        return RouteDecision(heavy, True, "near-threshold execution estimate")
    return RouteDecision(
        "needs_clarification",
        False,
        "uncertain estimate spans the near-threshold boundary and prevents dispatch",
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _relative_path(value: str, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise BoundaryError(f"{label} must be a nonblank relative path")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BoundaryError(f"{label} escapes its approved root")
    if any(any(character in part for character in "*?[]") for part in path.parts):
        raise BoundaryError(f"{label} must not be a glob")
    return path


def _assert_no_symlink_components(root: Path, relative: Path, *, leaf_must_exist: bool) -> Path:
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise BoundaryError(f"symlinked path is not allowed: {relative}")
    if leaf_must_exist and not candidate.is_file():
        raise BoundaryError(f"approved input is not a regular file: {relative}")
    resolved = candidate.resolve(strict=leaf_must_exist)
    if not _is_within(resolved, root):
        raise BoundaryError(f"path resolves outside approved root: {relative}")
    return candidate


def _directory_manifest(root: Path, *, label: str) -> tuple[dict[str, str], list[str]]:
    """Hash a regular-file tree and reject symlinks or non-file entries."""

    if root.is_symlink() or not root.is_dir():
        return {}, [f"{label}_directory_unavailable"]
    manifest: dict[str, str] = {}
    errors: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            errors.append(f"symlinked_{label}")
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            errors.append(f"nonregular_{label}")
            continue
        relative = path.relative_to(root)
        try:
            regular_path = _assert_no_symlink_components(root, relative, leaf_must_exist=True)
            manifest[relative.as_posix()] = hashlib.sha256(regular_path.read_bytes()).hexdigest()
        except (BoundaryError, OSError):
            errors.append(f"unable_to_hash_{label}")
    return manifest, sorted(set(errors))


def _source_worktree_root(source_root: Path) -> Path:
    """Use a repository root when visible so a nested stage cannot touch it."""

    current = source_root
    while True:
        if (current / ".git").exists() or (current / ".git").is_symlink():
            return current
        if current.parent == current:
            return source_root
        current = current.parent


def _stage_root(request: DispatchRequest, source_boundary: Path) -> Path:
    requested = Path(request.stage_root)
    if requested.is_symlink():
        raise BoundaryError("stage root may not be a symlink")
    parent = requested.parent
    if not parent.exists() or not parent.is_dir():
        raise BoundaryError("stage root parent must already exist")
    candidate = requested.resolve(strict=False)
    if _is_within(candidate, source_boundary) or _is_within(source_boundary, candidate):
        raise BoundaryError("stage root must be disjoint from the source working tree")
    if requested.exists():
        if not requested.is_dir() or any(requested.iterdir()):
            raise BoundaryError("stage root must be a new or empty directory")
    else:
        requested.mkdir(mode=0o700)
    root = requested.resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise BoundaryError("stage root is not a real directory")
    return root


def _continuation_attempt_errors(value: Any, index: int) -> list[str]:
    """Validate one preserved earlier attempt inside a cumulative lineage."""

    if not isinstance(value, Mapping):
        return ["invalid_continuation_attempt"]
    attempt = dict(value)
    expected = {
        "route",
        "failure_category",
        "stage_root",
        "receipt_digest",
        "brief_digest",
        "origin",
        "partial_manifest",
    }
    if set(attempt) != expected:
        return ["invalid_continuation_attempt"]
    errors: list[str] = []
    route = attempt.get("route")
    if not isinstance(route, str) or not route.strip():
        errors.append("invalid_continuation_attempt_route")
    if attempt.get("failure_category") not in CONTINUATION_FAILURE_CATEGORIES:
        errors.append("invalid_continuation_attempt_failure_category")
    if not isinstance(attempt.get("stage_root"), str) or not attempt["stage_root"].strip():
        errors.append("invalid_continuation_attempt_stage_root")
    for field in ("receipt_digest", "brief_digest"):
        if not _is_sha256_digest(attempt.get(field)):
            errors.append(f"invalid_continuation_attempt_{field}")
    # The origin directory name is derived, single-segment, and bound to the
    # attempt's position, so carried partial files always have one unambiguous
    # provenance under reference/partial-outputs.
    if not isinstance(route, str) or attempt.get("origin") != continuation_attempt_origin(
        index, route
    ):
        errors.append("invalid_continuation_attempt_origin")
    _require_hash_manifest(
        attempt.get("partial_manifest"),
        "invalid_continuation_attempt_partial_manifest",
        errors,
    )
    return sorted(set(errors))


def continuation_manifest_errors(value: Any) -> list[str]:
    """Validate one cumulative continuation lineage without trusting a caller."""

    if not isinstance(value, Mapping):
        return ["invalid_continuation_manifest"]
    manifest = dict(value)
    expected = {
        "failure_category",
        "prior_route",
        "prior_stage_root",
        "prior_receipt_digest",
        "prior_brief_digest",
        "input_baseline",
        "partial_manifest",
        "attempts",
    }
    if set(manifest) != expected:
        return ["invalid_continuation_manifest"]
    errors: list[str] = []
    if manifest.get("failure_category") not in CONTINUATION_FAILURE_CATEGORIES:
        errors.append("invalid_continuation_failure_category")
    if not isinstance(manifest.get("prior_route"), str) or not manifest["prior_route"].strip():
        errors.append("invalid_continuation_prior_route")
    if not isinstance(manifest.get("prior_stage_root"), str) or not manifest["prior_stage_root"].strip():
        errors.append("invalid_continuation_prior_stage_root")
    for field in ("prior_receipt_digest", "prior_brief_digest"):
        if not _is_sha256_digest(manifest.get(field)):
            errors.append(f"invalid_continuation_{field}")
    baseline = _require_hash_manifest(
        manifest.get("input_baseline"),
        "invalid_continuation_input_baseline",
        errors,
    )
    if baseline is not None and not baseline:
        errors.append("invalid_continuation_input_baseline")
    _require_hash_manifest(
        manifest.get("partial_manifest"),
        "invalid_continuation_partial_manifest",
        errors,
    )
    attempts = manifest.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        errors.append("invalid_continuation_attempts")
        return sorted(set(errors))
    for index, attempt in enumerate(attempts, start=1):
        errors.extend(_continuation_attempt_errors(attempt, index))
    if errors:
        return sorted(set(errors))
    routes = [str(dict(attempt)["route"]) for attempt in attempts]
    if len(set(routes)) != len(routes):
        errors.append("continuation_lineage_repeats_a_route")
    # The immediately previous attempt is the last recorded one, so a lineage
    # cannot describe one failure while carrying another attempt's evidence.
    last = dict(attempts[-1])
    if (
        last["route"] != manifest["prior_route"]
        or last["failure_category"] != manifest["failure_category"]
        or last["stage_root"] != manifest["prior_stage_root"]
        or last["receipt_digest"] != manifest["prior_receipt_digest"]
        or last["brief_digest"] != manifest["prior_brief_digest"]
        or dict(last["partial_manifest"]) != dict(manifest["partial_manifest"])
    ):
        errors.append("continuation_lineage_does_not_match_the_previous_attempt")
    return sorted(set(errors))


def _stage_continuation_reference(
    context: ContinuationContext,
    root: Path,
    control: Path,
    input_manifest: Mapping[str, str],
) -> None:
    """Copy every earlier attempt's verified partials into labelled directories.

    The partials are never staged as inputs and never counted as completed work.
    Each attempt keeps its own ``reference/partial-outputs/<origin>`` directory,
    so useful files from an earlier provider survive even when a later provider
    fails before creating any output. Every recorded hash and the original input
    baseline must still hold, so a tampered or replayed lineage fails closed.
    """

    errors = continuation_manifest_errors(context.to_manifest())
    if errors:
        raise BoundaryError(f"continuation lineage is invalid: {','.join(errors)}")
    if dict(context.input_baseline) != dict(input_manifest):
        raise BoundaryError("continuation input baseline does not match the revalidated staged inputs")
    reference = root / "reference" / "partial-outputs"
    reference.mkdir(parents=True, exist_ok=False)
    for attempt in context.attempts:
        prior_root = Path(attempt.stage_root)
        if prior_root.is_symlink() or not prior_root.is_dir():
            raise BoundaryError("prior continuation stage is unavailable")
        prior_root = prior_root.resolve(strict=True)
        if _is_within(prior_root, root) or _is_within(root, prior_root):
            raise BoundaryError("continuation stage must be disjoint from the prior stage")
        prior_outputs = prior_root / "outputs"
        origin = _relative_path(attempt.origin, "continuation attempt origin")
        if len(origin.parts) != 1:
            raise BoundaryError("continuation attempt origin must be one directory name")
        for value in sorted(dict(attempt.partial_manifest)):
            relative = _relative_path(value, "continuation partial output")
            source = _assert_no_symlink_components(prior_outputs, relative, leaf_must_exist=True)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if digest != attempt.partial_manifest[value]:
                raise BoundaryError("continuation partial output changed after the failed attempt")
            destination = reference / origin / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            _assert_no_symlink_components(reference, origin / relative.parent, leaf_must_exist=False)
            shutil.copy2(source, destination, follow_symlinks=False)
    payload = {
        "label": "prior_partial_outputs_are_unverified_reference_only",
        "schema_version": 1,
        **context.to_manifest(),
    }
    (control / "continuation.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def prepare_stage(request: DispatchRequest) -> StageLayout:
    """Create a data-minimal stage before a model process can start."""

    _workflow_selection_context(request.workflow_selection)
    if request.nested_dispatch_requested:
        raise BoundaryError("nested dispatch is not supported")
    if not request.inputs:
        raise BoundaryError("a worker stage needs at least one explicit input")
    if not request.approved_outputs:
        raise BoundaryError("a worker stage needs at least one approved output")
    if not isinstance(request.brief, str) or not request.brief.strip():
        raise BoundaryError("worker brief must be nonblank")

    source_requested = Path(request.source_root)
    if source_requested.is_symlink() or not source_requested.is_dir():
        raise BoundaryError("source root must be a real directory")
    source_root = source_requested.resolve(strict=True)
    source_boundary = _source_worktree_root(source_root)
    root = _stage_root(request, source_boundary)
    inputs = root / "inputs"
    outputs = root / "outputs"
    workspace = root / "workspace"
    control = workspace / "control"
    for directory in (inputs, outputs, control):
        directory.mkdir(parents=True, exist_ok=False)

    seen_inputs: set[str] = set()
    for value in request.inputs:
        relative = _relative_path(value, "input")
        normalized = relative.as_posix()
        if normalized in seen_inputs:
            raise BoundaryError("duplicate staged input")
        seen_inputs.add(normalized)
        source_file = _assert_no_symlink_components(source_root, relative, leaf_must_exist=True)
        staged_file = inputs / relative
        staged_file.parent.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_components(inputs, relative.parent, leaf_must_exist=False)
        shutil.copy2(source_file, staged_file, follow_symlinks=False)

    input_manifest, input_errors = _directory_manifest(inputs, label="staged_input")
    if input_errors or set(input_manifest) != seen_inputs:
        raise BoundaryError("unable to establish the explicit staged input baseline")

    approved_outputs: list[str] = []
    for value in request.approved_outputs:
        relative = _relative_path(value, "approved output")
        normalized = relative.as_posix()
        if normalized in approved_outputs:
            raise BoundaryError("duplicate approved output")
        approved_outputs.append(normalized)

    if request.continuation is not None:
        _stage_continuation_reference(request.continuation, root, control, input_manifest)

    brief_path = control / "brief.txt"
    brief_path.write_text(request.brief, encoding="utf-8")
    brief_digest = hashlib.sha256(brief_path.read_bytes()).hexdigest()
    (control / "approved_outputs.json").write_text(
        json.dumps(approved_outputs, indent=2) + "\n", encoding="utf-8"
    )
    (control / "empty-mcp.json").write_text('{"mcpServers":{}}\n', encoding="utf-8")
    return StageLayout(
        root=root,
        workspace=workspace,
        inputs=inputs,
        outputs=outputs,
        control=control,
        source_root=source_root,
        approved_outputs=tuple(approved_outputs),
        input_manifest=input_manifest,
        brief_digest=brief_digest,
        workflow_selection=request.workflow_selection,
    )


def _toml_string(value: str) -> str:
    """JSON's string grammar is valid for the TOML strings used here."""

    return json.dumps(value)


def _permission_table(
    profile_name: str, profile: Mapping[str, Any], stage_root: Path | None = None
) -> str:
    if profile_name not in {"delegation_worker", "delegation_reviewer"}:
        raise ConfigError("unsupported permission profile")
    root_access = _nonblank(profile.get("root_access"), "permission profile root_access")
    network_enabled = _bool(profile.get("network_enabled"), "permission profile network_enabled")
    filesystem_entries = f'":root"={_toml_string(root_access)}'
    if profile_name == "delegation_worker":
        if stage_root is None:
            raise BoundaryError("worker permission profile needs a stage root")
        stage_access = _nonblank(profile.get("stage_access"), "permission profile stage_access")
        resolved_stage = stage_root.resolve(strict=False)
        if resolved_stage.is_symlink():
            raise BoundaryError("permission profile stage root may not be a symlink")
        filesystem_entries += f",{_toml_string(str(resolved_stage))}={_toml_string(stage_access)}"
    # The absolute path is deliberately a quoted TOML key inside a *single*
    # --config value. Dotted per-path config keys are not supported by Codex.
    return (
        f"permissions.{profile_name}={{filesystem={{{filesystem_entries}}},"
        f"network={{enabled={'true' if network_enabled else 'false'}}}}}"
    )


def _worker_prompt(workflow_selection: str) -> str:
    return (
        f"{_workflow_selection_context(workflow_selection)} Read and follow the hash-bound controller "
        "plan at control/controller-plan.json, then read the task only from control/brief.txt and the explicitly staged "
        "files under ../inputs. Do not change the approved route or plan. Write candidate files only under ../outputs at paths "
        "listed in control/approved_outputs.json. Do not modify inputs, the "
        "control directory, the ../reference directory, or any source workspace. Native multi-agent tools, MCP, "
        "apps, browser/web tools, external messages, and launching another model CLI "
        "are prohibited. Any labelled prior partial work under ../reference is unverified "
        "reference material, not completed work. For success, return exactly one JSON object with "
        "status='completed', changed_outputs as a list of relative approved path strings, "
        "self_tests as a nonempty list of evidence strings, limitations as an optional "
        "list of evidence strings, and "
        "nested_dispatch='not_attempted'. A blocked, failed, or clarification result "
        "must not claim completed. This policy does not claim "
        "to stop a malicious shell command; the actual write boundary is the explicit "
        "Codex permission profile for the staging root."
    )


def _claude_absolute_rule(tool: str, path: Path) -> str:
    """Build one Claude permission rule; absolute rules begin with a double slash."""

    return f"{tool}(/{path})"


def _claude_file_tool_rules(
    stage_root: Path, approved_outputs: Sequence[str] | None
) -> tuple[str, ...]:
    """Confine Claude file tools to the stage and to exact approved outputs.

    Read/Glob/Grep may reach the whole stage, including its inputs, reference,
    and outputs directories. Write and Edit are enumerated per approved output
    path, so the child cannot create any other file even inside the stage.
    """

    if not approved_outputs:
        raise BoundaryError("a Claude worker needs its exact approved-output permission rules")
    root = Path(stage_root).resolve(strict=False)
    rules = [f"{tool}(/{root}/**)" for tool in ("Read", "Glob", "Grep")]
    seen: set[str] = set()
    for value in approved_outputs:
        relative = _relative_path(value, "approved output")
        normalized = relative.as_posix()
        if normalized in seen:
            raise BoundaryError("duplicate approved output")
        seen.add(normalized)
        target = root / "outputs" / relative
        rules.append(_claude_absolute_rule("Write", target))
        rules.append(_claude_absolute_rule("Edit", target))
    # The rule list is passed as one separated --allowedTools value, so a rule
    # containing a separator character would silently split into two different
    # rules. Fail closed instead of granting an unintended permission.
    for rule in rules:
        if "," in rule or any(character.isspace() for character in rule):
            raise BoundaryError(
                "Claude permission rules may not contain whitespace or commas; "
                "choose a stage root and approved output paths without them"
            )
    return tuple(rules)


def _claude_worker_prompt(stage_root: Path, workflow_selection: str) -> str:
    """Address the actual working directory with absolute staged paths.

    The Claude worker runs with the stage root as its working directory because
    ``--restricted`` confines its file tools to that directory, and inputs,
    outputs, reference, and workspace/control are all siblings inside it.
    """

    root = Path(stage_root).resolve(strict=False)
    control = root / "workspace" / "control"
    return (
        f"{_workflow_selection_context(workflow_selection)} Your working directory is "
        f"{root}. Read and follow the hash-bound controller plan at "
        f"{control / 'controller-plan.json'}, then read the task only from {control / 'brief.txt'} "
        f"and the explicitly staged files under {root / 'inputs'}. Do not change the approved route "
        f"or plan. Write candidate files only under {root / 'outputs'} at the paths listed in "
        f"{control / 'approved_outputs.json'}; your Write and Edit permission rules already name "
        "exactly those paths. Any labelled prior partial work under "
        f"{root / 'reference'} is unverified reference material, not completed work: verify it "
        "yourself instead of replaying it. Do not modify inputs, the control directory, the "
        "reference directory, or any source workspace. Nested agents, MCP, apps, browser/web "
        "tools, external messages, shell commands, and launching another model CLI are "
        "prohibited. Do not write a control file for your result. For success, return exactly one "
        "JSON object as your final message with status='completed', changed_outputs as a list of "
        "relative approved path strings, self_tests as a nonempty list of evidence strings, "
        "limitations as an optional list of evidence strings, and nested_dispatch='not_attempted'. "
        "A blocked, failed, or clarification result must not claim completed. This policy text is "
        "not the enforcement boundary: the enforced boundary is the restricted Claude file-tool "
        "set plus the exact approved-output Write/Edit permission rules in this command."
    )


def _bounded_timeout(config: DelegationConfig, value: Any, *, label: str) -> int:
    """Return one validated subprocess timeout from the bounded policy."""

    timeout = int(config.policy["default_timeout_seconds"]) if value is None else value
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise BoundaryError(f"{label} must be an integer")
    if timeout < 1 or timeout > int(config.policy["max_timeout_seconds"]):
        raise BoundaryError(f"{label} is outside the configured bounded range")
    return timeout


def _append_codex_native_tool_restrictions(command: list[str]) -> None:
    """Disable native agent/app/web surfaces independently of the child prompt."""

    for setting in (
        "agents.enabled=false",
        "features.multi_agent=false",
        "features.apps=false",
        "features.hooks=false",
        'web_search="disabled"',
        'approval_policy="never"',
    ):
        command.extend(["--config", setting])


def _codex_base_command(config: DelegationConfig, binding: Binding, stage_root: Path) -> list[str]:
    launcher = config.launchers["codex"]
    command = list(_command(launcher.get("command"), "launchers.codex"))
    if launcher["ignore_user_config"]:
        command.append("--ignore-user-config")
    if launcher["ephemeral"]:
        command.append("--ephemeral")
    if launcher["skip_git_repo_check"]:
        command.append("--skip-git-repo-check")
    # Do not use --sandbox here. On the supported local CLI it overrides the
    # explicit default_permissions profile and can re-enable broad temp writes.
    command.extend(["--config", 'default_permissions="delegation_worker"'])
    command.extend(
        [
            "--config",
            _permission_table(
                "delegation_worker",
                config.permission_profiles["delegation_worker"],
                stage_root,
            ),
        ]
    )
    _append_codex_native_tool_restrictions(command)
    command.extend(["--config", f"model={_toml_string(binding.model)}"])
    command.extend(["--config", f"model_reasoning_effort={_toml_string(binding.effort)}"])
    if binding.provider is not None:
        provider = config.providers[binding.provider]
        command.extend(["--config", f"model_provider={_toml_string(binding.provider)}"])
        for codex_key, config_key in (
            ("name", "name"),
            ("base_url", "base_url"),
            ("wire_api", "wire_api"),
            ("env_key", "environment_key"),
        ):
            command.extend(
                [
                    "--config",
                    f"model_providers.{binding.provider}.{codex_key}="
                    f"{_toml_string(_nonblank(provider[config_key], config_key))}",
                ]
            )
    if launcher["json_output"]:
        command.append("--json")
    return command


def _claude_worker_command(
    config: DelegationConfig,
    binding: Binding,
    stage_root: Path,
    *,
    workflow_selection: str,
    approved_outputs: Sequence[str] | None,
) -> tuple[str, ...]:
    """Build the restricted Claude worker command with exact output rules.

    Only verified flags are used. There is no shell tool, no global permission
    bypass, no bare mode, and no emptied HOME: the standard Claude login stays
    usable while inherited settings cannot widen the file-tool rights granted
    here.
    """

    prompt = _claude_worker_prompt(stage_root, workflow_selection)
    rules = _claude_file_tool_rules(stage_root, approved_outputs)
    launcher = config.launchers["claude"]
    command = list(_command(launcher.get("command"), "launchers.claude"))
    command.extend(["-p", "--model", binding.model, "--effort", binding.effort])
    if launcher["safe_mode"]:
        command.append("--safe-mode")
    if launcher["restricted"]:
        command.append("--restricted")
    if launcher["no_chrome"]:
        command.append("--no-chrome")
    if launcher["no_session_persistence"]:
        command.append("--no-session-persistence")
    if launcher["strict_mcp_config"]:
        command.extend(
            ["--strict-mcp-config", "--mcp-config", str(launcher["empty_mcp_config"])]
        )
    command.extend(
        [
            "--tools",
            str(launcher["worker_tools"]),
            "--allowedTools",
            ",".join(rules),
            "--permission-mode",
            str(launcher["worker_permission_mode"]),
            "--output-format",
            "json",
            prompt,
        ]
    )
    return tuple(command)


def build_worker_command(
    config: DelegationConfig,
    route: str,
    stage_root: Path,
    *,
    workflow_selection: str | None,
    approved_outputs: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Build, but do not execute, the direct worker command for one route."""

    binding = config.resolve_worker_binding(route)
    root = Path(stage_root).resolve(strict=False)
    if binding.launcher == "codex":
        command = _codex_base_command(config, binding, root)
        command.extend(
            [
                "--output-last-message",
                str(root / "workspace" / "control" / "final-message.json"),
                _worker_prompt(workflow_selection),
            ]
        )
        return tuple(command)
    if binding.launcher == "claude":
        _workflow_selection_context(workflow_selection)
        return _claude_worker_command(
            config,
            binding,
            root,
            workflow_selection=str(workflow_selection),
            approved_outputs=approved_outputs,
        )
    raise ConfigError("unified workers require the Codex or Claude launcher")


def build_reviewer_command(
    config: DelegationConfig,
    host: str,
    review_root: Path,
    *,
    workflow_selection: str | None,
) -> tuple[str, ...]:
    """Build a fresh read-only reviewer command using the host binding reference."""

    binding = config.resolve_host_binding(host, "reviewer")
    root = Path(review_root).resolve(strict=False)
    evidence_path = root / "review-evidence.json"
    prompt = (
        f"{_workflow_selection_context(workflow_selection)} Perform an independent read-only review of the evidence at {evidence_path}, the controller "
        f"brief at {root / 'controller-brief.txt'}, baseline inputs under {root / 'baseline-inputs'}, "
        f"the controller receipt at {root / 'controller-receipt.json'}, the hash-bound controller plan "
        f"at {root / 'controller-plan.json'}, and candidate artifacts "
        f"under {root / 'candidate-outputs'}. Do not mutate files. "
        "Return exactly one JSON object containing status (approved, rejected, or failed), "
        "reviewed_worker_digest, reviewed_output_manifest, reviewed_brief_digest, "
        "reviewed_input_manifest, reviewed_workflow_selection, reviewed_controller_receipt_digest, "
        "reviewed_controller_plan_digest, fresh_process=true, and "
        "evidence as a nonempty list of strings."
    )
    if host == "codex":
        launcher = config.launchers["codex"]
        command = list(_command(launcher.get("command"), "launchers.codex"))
        if launcher["ignore_user_config"]:
            command.append("--ignore-user-config")
        if launcher["ephemeral"]:
            command.append("--ephemeral")
        if launcher["skip_git_repo_check"]:
            command.append("--skip-git-repo-check")
        command.extend(["--config", 'default_permissions="delegation_reviewer"'])
        command.extend(
            [
                "--config",
                _permission_table(
                    "delegation_reviewer",
                    config.permission_profiles["delegation_reviewer"],
                ),
            ]
        )
        _append_codex_native_tool_restrictions(command)
        command.extend(["--config", f"model={_toml_string(binding.model)}"])
        command.extend(["--config", f"model_reasoning_effort={_toml_string(binding.effort)}"])
        if launcher["json_output"]:
            command.append("--json")
        command.extend(
            [
                prompt,
            ]
        )
        return tuple(command)
    if host == "claude":
        launcher = config.launchers["claude"]
        command = list(_command(launcher.get("command"), "launchers.claude"))
        command.extend(["-p", "--model", binding.model, "--effort", binding.effort, "--output-format", "json"])
        if launcher["no_chrome"]:
            command.append("--no-chrome")
        if launcher["strict_mcp_config"]:
            command.extend(["--strict-mcp-config", "--mcp-config", str(root / "empty-mcp.json")])
        if launcher["no_session_persistence"]:
            command.append("--no-session-persistence")
        command.extend(
            [
                "--permission-mode",
                str(launcher["permission_mode"]),
                "--tools",
                "Read,Glob,Grep",
                "--disallowedTools",
                "Agent,Task,ListAgents,SendMessage,Write,Edit,Bash",
                prompt,
            ]
        )
        return tuple(command)
    raise ConfigError(f"unsupported reviewer host: {host}")


def _controller_request_manifest(
    config: DelegationConfig,
    request: DispatchRequest,
    layout: StageLayout,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "config_digest": config.config_digest,
        "controller_host": request.controller_host,
        "route": request.route,
        "workflow_selection": layout.workflow_selection,
        "brief_digest": layout.brief_digest,
        "input_manifest": dict(layout.input_manifest),
        "approved_outputs": list(layout.approved_outputs),
    }
    if request.continuation is not None:
        manifest["continuation"] = request.continuation.to_manifest()
    return manifest


def build_controller_command(
    config: DelegationConfig,
    host: str,
    layout: StageLayout,
    request_manifest: Mapping[str, Any],
    *,
    workflow_selection: str | None,
) -> tuple[str, ...]:
    """Build the fresh highest-binding planning command before a worker starts."""

    binding = config.resolve_host_binding(host, "controller")
    root = layout.root
    expected = json.dumps(dict(request_manifest), sort_keys=True, separators=(",", ":"))
    prompt = (
        f"{_workflow_selection_context(workflow_selection)} Perform the required independent "
        "controller planning and route assessment before any worker begins. Read the non-sensitive "
        "task brief only from control/brief.txt, explicitly staged inputs from ../inputs, and "
        "approved outputs from control/approved_outputs.json. When the request manifest carries a "
        "continuation block, also read control/continuation.json and treat the referenced partial "
        "outputs as unverified prior work. Do not mutate files, launch children, "
        "send messages, or change the requested route automatically. Assess whether the requested "
        "route is suitable. Return exactly one JSON object with status (approved, rejected, "
        "needs_clarification, or failed), route, request_manifest, plan_steps as a nonempty list "
        "of actionable strings, evidence as a nonempty list of strings, limitations as a list of "
        "strings, and fresh_process=true. To approve, route and request_manifest must exactly match: "
        f"{expected}"
    )
    if host == "codex":
        launcher = config.launchers["codex"]
        command = list(_command(launcher.get("command"), "launchers.codex"))
        if launcher["ignore_user_config"]:
            command.append("--ignore-user-config")
        if launcher["ephemeral"]:
            command.append("--ephemeral")
        if launcher["skip_git_repo_check"]:
            command.append("--skip-git-repo-check")
        command.extend(["--config", 'default_permissions="delegation_reviewer"'])
        command.extend(
            [
                "--config",
                _permission_table(
                    "delegation_reviewer",
                    config.permission_profiles["delegation_reviewer"],
                ),
            ]
        )
        _append_codex_native_tool_restrictions(command)
        command.extend(["--config", f"model={_toml_string(binding.model)}"])
        command.extend(["--config", f"model_reasoning_effort={_toml_string(binding.effort)}"])
        if launcher["json_output"]:
            command.append("--json")
        command.append(prompt)
        return tuple(command)
    if host == "claude":
        launcher = config.launchers["claude"]
        command = list(_command(launcher.get("command"), "launchers.claude"))
        command.extend(["-p", "--model", binding.model, "--effort", binding.effort, "--output-format", "json"])
        if launcher["no_chrome"]:
            command.append("--no-chrome")
        if launcher["strict_mcp_config"]:
            command.extend(
                ["--strict-mcp-config", "--mcp-config", str(layout.control / "empty-mcp.json")]
            )
        if launcher["no_session_persistence"]:
            command.append("--no-session-persistence")
        command.extend(
            [
                "--permission-mode",
                str(launcher["permission_mode"]),
                "--tools",
                "Read,Glob,Grep",
                "--disallowedTools",
                "Agent,Task,ListAgents,SendMessage,Write,Edit,Bash",
                prompt,
            ]
        )
        return tuple(command)
    raise ConfigError(f"unsupported controller host: {host}")


def _dotenv_value(raw_value: str) -> str:
    """Parse one literal dotenv value without expansion or shell execution."""

    value = raw_value.strip()
    if not value:
        return ""
    if value[0] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        if end < 0:
            raise BoundaryError("credential_file_malformed")
        parsed = value[1:end]
        remainder = value[end + 1 :].strip()
        if remainder and not remainder.startswith("#"):
            raise BoundaryError("credential_file_malformed")
        return parsed
    for index, character in enumerate(value):
        if character == "#" and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value


def _dotenv_target_value(path: Path, target_key: str) -> str:
    """Read only the configured key from one literal dotenv file."""

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise BoundaryError("credential_file_malformed") from None
    except OSError:
        raise BoundaryError("credential_file_unreadable") from None

    found = False
    resolved_value: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "export" or line.startswith("export ") or line.startswith("export\t"):
            line = line[len("export") :].lstrip()
            if not line:
                raise BoundaryError("credential_file_malformed")
        if "=" not in line:
            raise BoundaryError("credential_file_malformed")
        key_raw, value_raw = line.split("=", 1)
        key = key_raw.strip()
        if not _environment_name(key):
            raise BoundaryError("credential_file_malformed")
        if key != target_key:
            continue
        if found:
            raise BoundaryError("credential_file_duplicate_key")
        parsed = _dotenv_value(value_raw)
        if not parsed:
            raise BoundaryError("credential_file_empty")
        resolved_value = parsed
        found = True
    if not found:
        raise BoundaryError("credential_file_missing_key")
    assert resolved_value is not None
    return resolved_value


def _child_environment(
    config: DelegationConfig,
    binding: Binding,
    layout: StageLayout,
    environment: Mapping[str, str] | None,
) -> dict[str, str]:
    source = dict(os.environ if environment is None else environment)
    keys = CLAUDE_ENVIRONMENT_KEYS if binding.launcher == "claude" else CODEX_ENVIRONMENT_KEYS
    result = {
        key: source[key]
        for key in keys
        if isinstance(source.get(key), str) and source[key]
    }
    # --ignore-user-config isolates policy settings, while preserving Codex's
    # supported existing authentication location when the local CLI needs it.
    # Do not replace HOME/CODEX_HOME with an empty directory: that would make
    # the standard Terra binding unusable and is not an auth isolation claim.
    # A Claude worker keeps only the standard login location under HOME and
    # never receives Codex or provider credentials.
    if binding.provider is not None:
        provider = config.providers[binding.provider]
        key_name = _nonblank(provider["environment_key"], "provider environment_key")
        secret = source.get(key_name)
        if not isinstance(secret, str) or not secret:
            credential_file = provider.get("credential_file")
            if isinstance(credential_file, str) and credential_file.strip():
                secret = _dotenv_target_value(Path(credential_file), key_name)
            else:
                raise BoundaryError(f"missing_provider_environment:{key_name}")
        result[key_name] = secret
    return result


def _list_stage_outputs(layout: StageLayout) -> tuple[list[str], list[str]]:
    files: list[str] = []
    errors: list[str] = []
    for path in sorted(layout.outputs.rglob("*")):
        if path.is_symlink():
            errors.append("symlinked_stage_output")
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            errors.append("nonregular_stage_output")
            continue
        resolved = path.resolve(strict=True)
        if not _is_within(resolved, layout.outputs):
            errors.append("stage_output_escaped")
            continue
        relative = path.relative_to(layout.outputs).as_posix()
        if relative not in layout.approved_outputs:
            errors.append("unapproved_stage_output")
            continue
        files.append(relative)
    return files, sorted(set(errors))


def _output_manifest(layout: StageLayout, outputs: Sequence[str]) -> tuple[dict[str, str], list[str]]:
    """Hash exact staged outputs after rejecting symlinks and path escape."""

    manifest: dict[str, str] = {}
    errors: list[str] = []
    for value in sorted(outputs):
        try:
            relative = _relative_path(value, "approved output")
            path = _assert_no_symlink_components(layout.outputs, relative, leaf_must_exist=True)
            manifest[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        except (BoundaryError, OSError):
            errors.append("unable_to_hash_staged_output")
    return manifest, sorted(set(errors))


def _completion_report(path: Path, approved_outputs: Sequence[str]) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.is_file():
        return None, ["missing_completion_report"]
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, ["invalid_completion_report"]
    if not isinstance(report, Mapping):
        return None, ["invalid_completion_report"]
    status = report.get("status")
    if status not in {"completed", "blocked", "failed", "needs_clarification"}:
        return None, ["invalid_completion_report"]
    changed = report.get("changed_outputs")
    tests = report.get("self_tests")
    limitations = report.get("limitations", [])
    nested = report.get("nested_dispatch")
    if not isinstance(changed, list) or not all(isinstance(item, str) and item in approved_outputs for item in changed):
        return None, ["invalid_completion_report"]
    if not isinstance(tests, list) or not all(isinstance(item, str) and item.strip() for item in tests):
        return None, ["invalid_completion_report"]
    if not isinstance(limitations, list) or not all(
        isinstance(item, str) and item.strip() for item in limitations
    ):
        return None, ["invalid_completion_report"]
    if nested != "not_attempted":
        return None, ["nested_dispatch_reported"]
    normalized = dict(report)
    normalized["limitations"] = list(limitations)
    return normalized, []


Runner = Callable[..., subprocess.CompletedProcess[str]]


def acquire_direct_worker_lease(
    config: DelegationConfig,
    ledger_dir: Path,
    dispatch_id: str,
) -> DispatchLease:
    """Reserve one of the configured direct-worker slots under a shared ledger.

    The primary controller must use the same ledger directory for concurrent
    dispatches. Stale or malformed leases fail closed and require recovery
    inspection rather than automatic deletion.
    """

    if not isinstance(dispatch_id, str) or not dispatch_id or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        for character in dispatch_id
    ):
        raise BoundaryError("dispatch_id must be a nonblank safe identifier")
    directory = Path(ledger_dir)
    if directory.is_symlink():
        raise BoundaryError("dispatch ledger may not be a symlink")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not directory.is_dir():
        raise BoundaryError("dispatch ledger is not a directory")
    lock_path = directory / ".lock"
    lease_path = directory / f"{dispatch_id}.lease"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            leases = sorted(directory.glob("*.lease"))
            if any(path.is_symlink() or not path.is_file() for path in leases):
                raise BoundaryError("dispatch ledger contains an unsafe lease")
            if len(leases) >= int(config.policy["direct_worker_cap"]):
                raise BoundaryError("direct_worker_cap_reached")
            if lease_path.exists() or lease_path.is_symlink():
                raise BoundaryError("dispatch_id already has a lease")
            descriptor = os.open(lease_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as lease:
                json.dump({"dispatch_id": dispatch_id}, lease)
                lease.write("\n")
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return DispatchLease(lease_path)


def _worker_receipt_base(
    config: DelegationConfig,
    route: str,
    layout: StageLayout,
    controller_host: str,
    continuation: ContinuationContext | None = None,
) -> dict[str, Any]:
    binding = config.resolve_worker_binding(route)
    try:
        permission_profile, write_boundary = WORKER_BOUNDARIES[binding.launcher]
    except KeyError as exc:
        raise ConfigError(f"unsupported worker launcher: {binding.launcher}") from exc
    return {
        "schema_version": 1,
        "kind": "worker",
        "route": route,
        "binding": binding.name,
        "requested_model": binding.model,
        "requested_effort": binding.effort,
        "actual_model": "unobserved",
        "actual_effort": "unobserved",
        "observed_model_usage": {
            "primary_model": "unobserved",
            "models": [],
            "auxiliary_models": [],
            "effort": "unobserved",
        },
        "sandbox": binding.sandbox,
        "launcher": binding.launcher,
        "permission_profile": permission_profile,
        "write_boundary": write_boundary,
        "file_tool_rules": [],
        "stage_root": str(layout.root),
        "source_root": str(layout.source_root),
        "input_manifest": dict(layout.input_manifest),
        "brief_digest": layout.brief_digest,
        "workflow_selection": layout.workflow_selection,
        "controller_host": controller_host,
        "controller_receipt": None,
        "controller_receipt_digest": None,
        "controller_plan_digest": None,
        "continuation": None if continuation is None else continuation.to_manifest(),
        "approved_outputs": list(layout.approved_outputs),
        "changed_outputs": [],
        "output_manifest": {},
        "self_tests": [],
        "limitations": [],
        "nested_dispatch": "not_attempted",
        "child_exit_code": "unobserved",
        "failure_category": "none",
        "status": "planned",
        "errors": [],
        "evidence": ["staged execution boundary prepared"],
    }


def _fail_receipt(
    receipt: dict[str, Any],
    *,
    errors: Sequence[str],
    evidence: Sequence[str],
    status: str = "failed",
    failure_category: str = "generic_failure",
) -> dict[str, Any]:
    """Record one bounded worker failure with its coarse failure category."""

    receipt["status"] = status
    receipt["errors"] = list(errors)
    receipt["evidence"] = list(evidence)
    receipt["failure_category"] = failure_category if status == "failed" else "none"
    return receipt


def _decoded_json_objects(stdout: Any) -> list[Mapping[str, Any]]:
    """Return JSON objects printed by a CLI, whole output first, then last line."""

    if not isinstance(stdout, str) or not stdout.strip():
        return []
    objects: list[Mapping[str, Any]] = []
    candidates = [stdout.strip(), *reversed([line for line in stdout.splitlines() if line.strip()])]
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            objects.append(parsed)
    return objects


def _claude_result_envelope(stdout: Any) -> Mapping[str, Any] | None:
    """Return the Claude CLI's top-level result or error envelope, if present."""

    for candidate in _decoded_json_objects(stdout):
        if any(key in candidate for key in ("result", "is_error", "subtype", "modelUsage", "error")):
            return candidate
    return None


def _fenced_blocks(text: str) -> list[str]:
    """Return the contents of fenced code blocks in a final assistant message."""

    blocks: list[str] = []
    remainder = text
    while True:
        start = remainder.find("```")
        if start < 0:
            break
        newline = remainder.find("\n", start)
        if newline < 0:
            break
        end = remainder.find("```", newline)
        if end < 0:
            break
        blocks.append(remainder[newline + 1 : end])
        remainder = remainder[end + 3 :]
    return blocks


def _report_from_text(value: Any) -> Mapping[str, Any] | None:
    """Extract one JSON completion object from plain or fenced report text."""

    if not isinstance(value, str) or not value.strip():
        return None
    candidates = [value, *_fenced_blocks(value)]
    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end > start:
        candidates.append(value[start : end + 1])
    for candidate in candidates:
        outcome = _outcome_from_text(candidate)
        if outcome is not None:
            return outcome
    return None


def _claude_worker_report(envelope: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Read the structured final report from a successful Claude envelope."""

    if not isinstance(envelope, Mapping) or envelope.get("is_error") is True:
        return None
    for key in ("result", "output_text", "text", "content"):
        report = _report_from_text(envelope.get(key))
        if report is not None:
            return report
    return None


def _claude_observed_models(
    envelope: Mapping[str, Any] | None, requested_model: str
) -> dict[str, Any]:
    """Separate the requested primary model from internal auxiliary accounting.

    Claude's modelUsage can include internal Haiku accounting beside the primary
    model. An auxiliary entry is never reported as the primary model, and the
    primary stays unobserved unless the requested model itself appears.
    """

    models: list[str] = []
    if isinstance(envelope, Mapping):
        usage = envelope.get("modelUsage")
        if isinstance(usage, Mapping):
            models = sorted(
                str(name) for name in usage if isinstance(name, str) and name.strip()
            )
    primary = requested_model if requested_model in models else "unobserved"
    return {
        "primary_model": primary,
        "models": models,
        "auxiliary_models": [name for name in models if name != requested_model],
        "effort": "unobserved",
    }


def _capacity_failure_text(envelope: Mapping[str, Any]) -> str:
    """Collect only the designated top-level failure fields of an envelope."""

    parts: list[str] = []
    for key in CAPACITY_ENVELOPE_FIELDS:
        value = envelope.get(key)
        if isinstance(value, str):
            parts.append(value)
    error = envelope.get("error")
    if isinstance(error, str):
        parts.append(error)
    elif isinstance(error, Mapping):
        for key in ("type", "message"):
            value = error.get(key)
            if isinstance(value, str):
                parts.append(value)
    # The installed Claude CLI can report a failed result with useful capacity
    # evidence only in this top-level list. Do not recursively scan it: a
    # nested tool result or arbitrary structured payload is not CLI failure
    # evidence and must never start a continuation.
    errors = envelope.get("errors")
    if isinstance(errors, list):
        for entry in errors:
            if isinstance(entry, str):
                parts.append(entry)
            elif isinstance(entry, Mapping):
                for key in CAPACITY_ERROR_ENTRY_FIELDS:
                    value = entry.get(key)
                    if isinstance(value, str):
                        parts.append(value)
    return "\n".join(parts).lower()


def classify_capacity_failure(envelope: Mapping[str, Any] | None) -> str | None:
    """Classify only a genuine top-level CLI or provider capacity failure.

    A successful envelope is never inspected, so worker prose, tool output,
    staged inputs, and prompts cannot produce a classification. Auth failures,
    permission denials, launch/network errors, generic timeouts, malformed
    completions, ordinary max-turn limits, and per-response output truncation
    are excluded and stay generic failures.
    """

    if not isinstance(envelope, Mapping):
        return None
    is_error = envelope.get("is_error") is True
    provider_error = envelope.get("type") == "error" and isinstance(
        envelope.get("error"), (str, Mapping)
    )
    if not (is_error or provider_error):
        return None
    subtype = envelope.get("subtype")
    if isinstance(subtype, str) and subtype.strip().lower() in CAPACITY_EXCLUDED_SUBTYPES:
        return None
    text = _capacity_failure_text(envelope)
    if not text.strip():
        return None
    if any(marker in text for marker in CAPACITY_EXCLUSION_MARKERS):
        return None
    for category, markers in CAPACITY_FAILURE_MARKERS:
        if any(marker in text for marker in markers):
            return category
    return None


def _codex_failure_envelopes(stdout: Any) -> list[Mapping[str, Any]]:
    """Return only genuine top-level Codex turn-failure or error envelopes.

    Streamed ``item.*`` events, agent messages, and any other successful event
    are ignored, so tool output and worker prose can never be read as provider
    failure evidence.
    """

    envelopes: list[Mapping[str, Any]] = []
    for candidate in _decoded_json_objects(stdout):
        kind = candidate.get("type")
        if not isinstance(kind, str):
            continue
        if kind.strip().lower().replace("_", ".") not in CODEX_FAILURE_ENVELOPE_TYPES:
            continue
        envelopes.append(candidate)
    return envelopes


def _provider_failure_evidence(
    envelope: Mapping[str, Any]
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """Collect only the designated failure fields of one provider envelope."""

    strings: list[str] = []
    codes: list[str] = []
    statuses: list[str] = []

    def collect(source: Mapping[str, Any]) -> None:
        for key in BALANCE_ENVELOPE_STRING_FIELDS:
            value = source.get(key)
            if isinstance(value, str):
                strings.append(value)
        for key in BALANCE_ENVELOPE_CODE_FIELDS:
            value = source.get(key)
            if isinstance(value, str):
                codes.append(value.strip().lower())
        for key in BALANCE_ENVELOPE_STATUS_FIELDS:
            value = source.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                statuses.append(str(value))
            elif isinstance(value, str):
                statuses.append(value.strip())

    collect(envelope)
    error = envelope.get("error")
    if isinstance(error, str):
        strings.append(error)
    elif isinstance(error, Mapping):
        collect(error)
    return "\n".join(strings).lower(), tuple(codes), tuple(statuses)


def classify_provider_balance_failure(
    config: DelegationConfig,
    binding: Binding,
    stdout: Any,
) -> str | None:
    """Classify only a verified configured-provider insufficient-balance failure.

    Evidence must come from a top-level Codex turn-failure or provider error
    envelope for the *configured* provider endpoint, and must report both HTTP
    402 Payment Required and insufficient balance, either as the provider's own
    message or as a structured insufficient-balance code. A wrong endpoint, an
    authentication failure, a rate/quota/context failure, a transport error, a
    successful envelope, worker prose, tool output, and a generic or malformed
    payment failure are all excluded and stay ordinary failures.
    """

    if binding.provider is None:
        return None
    provider = config.providers.get(binding.provider)
    if not isinstance(provider, Mapping):
        return None
    endpoint = str(provider.get("base_url", "")).strip().rstrip("/").lower()
    if not endpoint:
        return None
    for envelope in _codex_failure_envelopes(stdout):
        text, codes, statuses = _provider_failure_evidence(envelope)
        if endpoint not in text:
            continue
        if any(marker in text for marker in BALANCE_EXCLUSION_MARKERS):
            continue
        payment_required = BALANCE_PAYMENT_REQUIRED_STATUS in text or any(
            status == BALANCE_PAYMENT_REQUIRED_STATUS for status in statuses
        )
        if not payment_required:
            continue
        if any(marker in text for marker in BALANCE_INSUFFICIENT_MARKERS) or any(
            code in BALANCE_STRUCTURED_CODES for code in codes
        ):
            return PROVIDER_BALANCE_FAILURE_CATEGORY
    return None


def run_staged_worker(
    config: DelegationConfig,
    request: DispatchRequest,
    *,
    runner: Runner = subprocess.run,
    controller_runner: Runner = subprocess.run,
    environment: Mapping[str, str] | None = None,
    dry_run: bool = False,
    ledger_dir: Path | None = None,
    dispatch_id: str | None = None,
) -> dict[str, Any]:
    """Stage a worker and run it only when the preflight can fail closed.

    A zero subprocess exit code is insufficient.  Completion additionally
    requires a structured child report, no reported nested dispatch, and no
    unexpected staged output.  A Claude worker returns that report in its result
    envelope; this controller parses and persists it rather than requiring the
    child to write a control file.  This function never copies stage output back
    to the source root; ``apply_reviewed_outputs`` is a separate controller step.
    """

    timeout = _bounded_timeout(config, request.timeout_seconds, label="timeout_seconds")
    # Resolve before staging so an invalid host reference cannot leave a stage
    # that appears ready even though required highest-binding control is absent.
    config.resolve_host_binding(request.controller_host, "controller")
    layout = prepare_stage(request)
    binding = config.resolve_worker_binding(request.route)
    command = build_worker_command(
        config,
        request.route,
        layout.root,
        workflow_selection=layout.workflow_selection,
        approved_outputs=layout.approved_outputs,
    )
    receipt = _worker_receipt_base(
        config,
        request.route,
        layout,
        request.controller_host,
        request.continuation,
    )
    receipt["command"] = list(command)
    receipt["process"] = {
        "launcher": binding.launcher,
        "exit_code": 127,
        "command_digest": hashlib.sha256(
            json.dumps(list(command), separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "output_digest": hashlib.sha256(b"").hexdigest(),
    }
    if binding.launcher == "claude":
        receipt["file_tool_rules"] = list(
            _claude_file_tool_rules(layout.root, layout.approved_outputs)
        )

    def finish(value: Mapping[str, Any]) -> dict[str, Any]:
        return _persist_worker_receipt(layout, value)

    # A continuation is allowed to reuse only a receipt snapshot that still
    # verifies at the moment this fresh stage is about to begin. This closes the
    # gap between eligibility evaluation and the next worker launch.
    if request.continuation is not None:
        try:
            _verify_source_baseline(layout)
            _verify_prior_attempts(config, layout, request.continuation.attempts)
        except BoundaryError as exc:
            return finish(
                _fail_receipt(
                    receipt,
                    errors=["continuation_ancestor_integrity_failed"],
                    evidence=[f"worker did not start because continuation ancestry failed: {exc}"],
                )
            )
    if dry_run:
        receipt["evidence"] = ["dry-run command constructed; no child process started"]
        return finish(receipt)

    try:
        child_environment = _child_environment(config, binding, layout, environment)
    except BoundaryError as exc:
        return finish(
            _fail_receipt(
                receipt,
                errors=[str(exc)],
                evidence=["worker did not start because launch preflight failed"],
            )
        )

    controller_receipt = run_controller_planner(
        config,
        request,
        layout,
        runner=controller_runner,
        environment=environment,
        timeout_seconds=timeout,
    )
    receipt["controller_receipt"] = controller_receipt
    receipt["controller_receipt_digest"] = receipt_digest(controller_receipt)
    if controller_receipt.get("status") != "approved":
        return finish(
            _fail_receipt(
                receipt,
                status=(
                    "needs_clarification"
                    if controller_receipt.get("status") == "needs_clarification"
                    else "failed"
                ),
                errors=["controller_plan_not_approved"],
                evidence=["worker did not start because the fresh controller plan was not approved"],
            )
        )
    request_manifest = _controller_request_manifest(config, request, layout)
    controller_manifest = controller_receipt.get("request_manifest")
    if not isinstance(controller_manifest, Mapping) or dict(controller_manifest) != request_manifest:
        return finish(
            _fail_receipt(
                receipt,
                errors=["controller_request_manifest_changed"],
                evidence=["worker did not start because the controller request changed"],
            )
        )
    try:
        _verify_controller_request(layout, request_manifest)
        receipt["controller_plan_digest"] = _write_controller_plan(
            layout,
            controller_receipt,
            request_manifest,
        )
    except (BoundaryError, OSError) as exc:
        return finish(
            _fail_receipt(
                receipt,
                errors=[f"controller_plan_write_failed:{type(exc).__name__}"],
                evidence=["worker did not start because controller plan persistence failed"],
            )
        )

    try:
        lease = acquire_direct_worker_lease(
            config,
            ledger_dir or (layout.root.parent / ".unified-delegation-leases"),
            dispatch_id or hashlib.sha256(str(layout.root).encode("utf-8")).hexdigest()[:16],
        )
    except BoundaryError as exc:
        return finish(
            _fail_receipt(
                receipt,
                errors=[str(exc)],
                evidence=["worker did not start because the direct-worker cap is exhausted"],
            )
        )

    # The Codex worker prompt addresses control/ and ../inputs relative to the
    # stage workspace. The Claude worker instead runs at the stage root, because
    # --restricted confines its file tools to the working directory and inputs,
    # outputs, reference, and workspace/control are siblings inside that root.
    worker_cwd = layout.root if binding.launcher == "claude" else layout.workspace
    try:
        completed = runner(
            command,
            cwd=str(worker_cwd),
            env=child_environment,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        lease.release()
        receipt["child_exit_code"] = 124
        receipt["process"]["exit_code"] = 124
        return finish(
            _fail_receipt(
                receipt,
                errors=["timeout"],
                evidence=["worker timed out; inspect the staged root before any recovery"],
            )
        )
    except OSError:
        lease.release()
        return finish(
            _fail_receipt(
                receipt,
                errors=["launch_failed"],
                evidence=["worker launcher did not start"],
            )
        )
    lease.release()

    receipt["child_exit_code"] = completed.returncode
    stdout = completed.stdout if isinstance(completed.stdout, str) else ""
    stderr = completed.stderr if isinstance(completed.stderr, str) else ""
    receipt["process"]["exit_code"] = completed.returncode
    receipt["process"]["output_digest"] = hashlib.sha256(
        (stdout + "\0" + stderr).encode("utf-8")
    ).hexdigest()
    try:
        _verify_controller_request(layout, request_manifest)
        _verify_controller_plan(
            layout,
            controller_receipt,
            request_manifest,
            receipt["controller_plan_digest"],
        )
    except BoundaryError:
        return finish(
            _fail_receipt(
                receipt,
                errors=["controller_plan_changed"],
                evidence=["worker result did not retain its hash-bound controller plan"],
            )
        )

    continuation_category: str | None = None
    if binding.launcher == "claude":
        envelope = _claude_result_envelope(stdout)
        receipt["observed_model_usage"] = _claude_observed_models(envelope, binding.model)
        if receipt["observed_model_usage"]["primary_model"] != "unobserved":
            receipt["actual_model"] = receipt["observed_model_usage"]["primary_model"]
        continuation_category = classify_capacity_failure(envelope)
        report_object = _claude_worker_report(envelope)
        if report_object is not None:
            try:
                report_path = _assert_no_symlink_components(
                    layout.control,
                    Path("final-message.json"),
                    leaf_must_exist=False,
                )
                report_path.write_text(
                    json.dumps(dict(report_object), sort_keys=True) + "\n", encoding="utf-8"
                )
            except (BoundaryError, OSError):
                pass
    elif binding.provider is not None:
        # Only the configured provider's own top-level failure envelope can
        # classify an insufficient-balance failure for the balance transition.
        continuation_category = classify_provider_balance_failure(config, binding, stdout)

    output_paths, boundary_errors = _list_stage_outputs(layout)
    output_manifest, manifest_errors = _output_manifest(layout, output_paths)
    boundary_errors.extend(manifest_errors)
    receipt["output_manifest"] = output_manifest
    final_path = layout.control / "final-message.json"
    report, report_errors = _completion_report(final_path, layout.approved_outputs)
    errors = boundary_errors + report_errors
    if completed.returncode != 0:
        errors.append("child_exit_nonzero")
    if report is not None:
        receipt["nested_dispatch"] = str(report["nested_dispatch"])
        receipt["changed_outputs"] = list(report["changed_outputs"])
        receipt["self_tests"] = list(report["self_tests"])
        receipt["limitations"] = list(report["limitations"])
        if sorted(receipt["changed_outputs"]) != sorted(output_paths):
            errors.append("completion_output_mismatch")
        if report["status"] == "completed" and not report["self_tests"]:
            errors.append("completed_without_self_tests")
        if not errors:
            receipt["status"] = str(report["status"])
            receipt["evidence"] = [
                f"structured child report status={report['status']}",
                f"staged outputs={len(output_paths)}",
                *[f"worker self-test: {item}" for item in receipt["self_tests"]],
                *[f"worker limitation: {item}" for item in receipt["limitations"]],
            ]
    if errors:
        receipt["status"] = "failed"
        receipt["errors"] = sorted(set(errors))
        receipt["evidence"] = ["worker result did not meet the completion gate"]
    receipt["failure_category"] = (
        (continuation_category or "generic_failure") if receipt["status"] == "failed" else "none"
    )
    return finish(receipt)


def _verify_source_baseline(layout: StageLayout) -> dict[str, str]:
    """Re-hash the original source files behind one staged input baseline."""

    manifest: dict[str, str] = {}
    for value, digest in sorted(dict(layout.input_manifest).items()):
        relative = _relative_path(value, "input")
        source_file = _assert_no_symlink_components(
            layout.source_root, relative, leaf_must_exist=True
        )
        actual = hashlib.sha256(source_file.read_bytes()).hexdigest()
        if actual != digest:
            raise BoundaryError("original source baseline changed after staging")
        manifest[relative.as_posix()] = actual
    return manifest


def _lineage_attempts(worker_receipt: Mapping[str, Any]) -> tuple[ContinuationAttempt, ...]:
    lineage = worker_receipt.get("continuation")
    if lineage is None:
        return ()
    return ContinuationContext.from_manifest(lineage).attempts


def _expected_prior_continuation(
    attempts: Sequence[ContinuationAttempt],
    input_manifest: Mapping[str, str],
) -> Mapping[str, Any] | None:
    """Rebuild the exact lineage an earlier continuation receipt must carry."""

    if not attempts:
        return None
    last = attempts[-1]
    return ContinuationContext(
        failure_category=last.failure_category,
        prior_route=last.route,
        prior_stage_root=last.stage_root,
        prior_receipt_digest=last.receipt_digest,
        prior_brief_digest=last.brief_digest,
        input_baseline=dict(input_manifest),
        partial_manifest=dict(last.partial_manifest),
        attempts=tuple(attempts),
    ).to_manifest()


def _verify_worker_controller_context(
    config: DelegationConfig,
    layout: StageLayout,
    worker_receipt: Mapping[str, Any],
) -> None:
    """Verify the receipt's persisted controller request, receipt, and plan."""

    controller_receipt = worker_receipt.get("controller_receipt")
    if not isinstance(controller_receipt, Mapping):
        raise BoundaryError("worker controller receipt is unavailable")
    controller_errors = validate_controller_receipt(config, controller_receipt)
    if controller_errors:
        raise BoundaryError("worker controller receipt is invalid")
    if controller_receipt.get("status") != "approved":
        raise BoundaryError("worker controller receipt is not approved")
    if worker_receipt.get("controller_receipt_digest") != receipt_digest(controller_receipt):
        raise BoundaryError("worker controller receipt changed after execution")
    if controller_receipt.get("host") != worker_receipt.get("controller_host"):
        raise BoundaryError("worker controller host does not match the worker receipt")
    if controller_receipt.get("route") != worker_receipt.get("route"):
        raise BoundaryError("worker controller route does not match the worker receipt")
    if controller_receipt.get("workflow_selection") != worker_receipt.get("workflow_selection"):
        raise BoundaryError("worker controller workflow does not match the worker receipt")
    request_manifest = controller_receipt.get("request_manifest")
    if not isinstance(request_manifest, Mapping):
        raise BoundaryError("worker controller request manifest is unavailable")
    expected = {
        "brief_digest": worker_receipt.get("brief_digest"),
        "input_manifest": worker_receipt.get("input_manifest"),
        "approved_outputs": worker_receipt.get("approved_outputs"),
        "continuation": worker_receipt.get("continuation"),
    }
    if any(request_manifest.get(key) != value for key, value in expected.items()):
        raise BoundaryError("worker controller request does not match the worker receipt")
    _verify_controller_request(layout, request_manifest)
    _verify_controller_plan(
        layout,
        controller_receipt,
        request_manifest,
        worker_receipt.get("controller_plan_digest"),
    )


def _verify_persisted_worker_receipt(
    config: DelegationConfig,
    layout: StageLayout,
    worker_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Require the on-disk worker snapshot to match the supplied receipt."""

    expected_digest = receipt_digest(worker_receipt)
    snapshot = _load_persisted_worker_receipt(layout.control, expected_digest)
    errors = validate_receipt(config, snapshot)
    if errors:
        raise BoundaryError("persisted worker receipt is invalid")
    if snapshot.get("stage_root") != str(layout.root):
        raise BoundaryError("persisted worker receipt stage root does not match")
    if snapshot.get("source_root") != str(layout.source_root):
        raise BoundaryError("persisted worker receipt source root does not match")
    return snapshot


def _verify_prior_attempts(
    config: DelegationConfig,
    layout: StageLayout,
    attempts: Sequence[ContinuationAttempt],
) -> None:
    """Re-verify every earlier attempt carried in a cumulative lineage.

    A later transition must not trust only the immediately previous attempt: an
    earlier stage, persisted receipt, controller request/receipt/plan, brief,
    staged input baseline, and each partial file it contributed must all still
    hash as recorded.
    """

    if len(attempts) >= len(config.workers):
        raise BoundaryError("continuation lineage exceeds the finite route chain")
    for index, attempt in enumerate(attempts):
        prior_root = Path(attempt.stage_root)
        if prior_root.is_symlink() or not prior_root.is_dir():
            raise BoundaryError(f"earlier attempt stage is unavailable: {attempt.origin}")
        prior_root = prior_root.resolve(strict=True)
        if prior_root == layout.root:
            raise BoundaryError(f"earlier attempt stage is not disjoint: {attempt.origin}")
        prior_control = prior_root / "workspace" / "control"
        if prior_control.is_symlink() or not prior_control.is_dir():
            raise BoundaryError(f"earlier attempt control is unavailable: {attempt.origin}")
        snapshot = _load_persisted_worker_receipt(prior_control, attempt.receipt_digest)
        snapshot_errors = validate_receipt(config, snapshot)
        if snapshot_errors:
            raise BoundaryError(f"earlier attempt receipt is invalid: {attempt.origin}")
        prior_layout = _layout_from_worker_receipt(snapshot)
        if prior_layout.root != prior_root:
            raise BoundaryError(f"earlier attempt receipt stage changed: {attempt.origin}")
        if prior_layout.source_root != layout.source_root:
            raise BoundaryError(f"earlier attempt source root changed: {attempt.origin}")
        if snapshot.get("status") != "failed":
            raise BoundaryError(f"earlier attempt is not a failed receipt: {attempt.origin}")
        if snapshot.get("route") != attempt.route:
            raise BoundaryError(f"earlier attempt route changed: {attempt.origin}")
        if snapshot.get("failure_category") != attempt.failure_category:
            raise BoundaryError(f"earlier attempt failure category changed: {attempt.origin}")
        if snapshot.get("brief_digest") != attempt.brief_digest:
            raise BoundaryError(f"earlier attempt brief digest changed: {attempt.origin}")
        if snapshot.get("input_manifest") != dict(layout.input_manifest):
            raise BoundaryError(f"earlier attempt input baseline changed: {attempt.origin}")
        if snapshot.get("continuation") != _expected_prior_continuation(
            attempts[:index], layout.input_manifest
        ):
            raise BoundaryError(f"earlier attempt lineage changed: {attempt.origin}")
        _verify_worker_controller_context(config, prior_layout, snapshot)
        _verify_source_baseline(prior_layout)
        prior_inputs, input_errors = _directory_manifest(
            prior_root / "inputs", label="staged_input"
        )
        if input_errors or prior_inputs != dict(layout.input_manifest):
            raise BoundaryError(f"earlier attempt staged inputs changed: {attempt.origin}")
        prior_brief = _assert_no_symlink_components(
            prior_root / "workspace" / "control",
            Path("brief.txt"),
            leaf_must_exist=True,
        )
        if hashlib.sha256(prior_brief.read_bytes()).hexdigest() != attempt.brief_digest:
            raise BoundaryError(f"earlier attempt brief changed: {attempt.origin}")
        prior_outputs = prior_root / "outputs"
        output_paths, output_errors = _list_stage_outputs(prior_layout)
        output_manifest, manifest_errors = _output_manifest(prior_layout, output_paths)
        if output_errors or manifest_errors or output_manifest != dict(attempt.partial_manifest):
            raise BoundaryError(f"earlier attempt partial output changed: {attempt.origin}")
        if snapshot.get("output_manifest") != dict(attempt.partial_manifest):
            raise BoundaryError(f"earlier attempt receipt output manifest changed: {attempt.origin}")
        for value, digest in sorted(dict(attempt.partial_manifest).items()):
            relative = _relative_path(value, "continuation partial output")
            path = _assert_no_symlink_components(prior_outputs, relative, leaf_must_exist=True)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise BoundaryError(
                    f"earlier attempt partial output changed: {attempt.origin}"
                )


def evaluate_continuation_eligibility(
    config: DelegationConfig,
    worker_receipt: Mapping[str, Any],
) -> tuple[str | None, str]:
    """Decide whether one failed attempt may continue on its configured edge.

    A route continues only on its own configured trigger: heavy-primary
    capacity exhaustion, or a verified configured-provider insufficient-balance
    failure. The receipt must be a validated failure whose controller plan,
    staged inputs, partial outputs, and original source baseline all still hash
    as recorded, and every earlier attempt in its cumulative lineage must still
    verify too. A route already used in this chain is never revisited, so the
    chain stays finite and the terminal fallback route ends it.
    """

    if not isinstance(worker_receipt, Mapping):
        return None, "invalid_worker_receipt"
    route = worker_receipt.get("route")
    if not isinstance(route, str) or route not in config.workers:
        return None, "invalid_worker_receipt"
    next_route, triggers = config.continuation_edge(route)
    if next_route is None:
        return None, "route_has_no_configured_continuation_edge"
    if worker_receipt.get("status") != "failed":
        return None, "worker_did_not_fail"
    category = worker_receipt.get("failure_category")
    if category not in triggers:
        return None, "failure_is_not_an_eligible_continuation_trigger"
    if validate_receipt(config, worker_receipt):
        return None, "failed_receipt_is_invalid"
    attempts: tuple[ContinuationAttempt, ...] = ()
    lineage = worker_receipt.get("continuation")
    if lineage is not None:
        lineage_errors = continuation_manifest_errors(lineage)
        if lineage_errors:
            return None, f"invalid_continuation_lineage:{','.join(lineage_errors)}"
        attempts = ContinuationContext.from_manifest(lineage).attempts
    attempted_routes = [attempt.route for attempt in attempts] + [route]
    if len(set(attempted_routes)) != len(attempted_routes):
        return None, "continuation_lineage_repeats_a_route"
    if next_route in attempted_routes:
        return None, "continuation_route_was_already_attempted"
    controller_receipt = worker_receipt.get("controller_receipt")
    if not isinstance(controller_receipt, Mapping) or controller_receipt.get("status") != "approved":
        return None, "failed_attempt_has_no_approved_controller_plan"
    request_manifest = controller_receipt.get("request_manifest")
    if not isinstance(request_manifest, Mapping):
        return None, "failed_attempt_has_no_controller_request_manifest"
    if receipt_digest(controller_receipt) != worker_receipt.get("controller_receipt_digest"):
        return None, "failed_attempt_controller_receipt_digest_mismatch"
    # The lineage carried into the next transition must be exactly the one the
    # fresh controller approved and the persisted plan digest covers.
    if request_manifest.get("continuation") != worker_receipt.get("continuation"):
        return None, "failed_attempt_continuation_lineage_is_not_the_approved_one"
    try:
        layout = _layout_from_worker_receipt(worker_receipt)
        _verify_persisted_worker_receipt(config, layout, worker_receipt)
        _verify_worker_controller_context(config, layout, worker_receipt)
        staged_inputs, input_errors = _directory_manifest(layout.inputs, label="staged_input")
        if input_errors or staged_inputs != dict(layout.input_manifest):
            return None, "staged_inputs_changed_after_the_failed_attempt"
        _verify_source_baseline(layout)
        _verify_prior_attempts(config, layout, attempts)
    except BoundaryError as exc:
        return None, f"failed_stage_integrity_check:{exc}"
    return str(category), "eligible"


CONTINUATION_BRIEF_MARKER = (
    "--- AUTOMATIC CONTINUATION CONTEXT (hash-bound, controller-authored) ---"
)


def _continuation_brief(original_brief: str, context: ContinuationContext) -> str:
    """Label all carried-over partial work without claiming it is complete."""

    # A second transition rebuilds the block from the original task text rather
    # than nesting one continuation notice inside another.
    base = original_brief.split(CONTINUATION_BRIEF_MARKER)[0].rstrip()
    lines: list[str] = []
    for attempt in context.attempts:
        partials = sorted(dict(attempt.partial_manifest))
        listed = ", ".join(partials) if partials else "none"
        lines.append(
            f"- attempt {attempt.origin}: route '{attempt.route}' stopped with failure "
            f"category '{attempt.failure_category}'; receipt digest "
            f"{attempt.receipt_digest}; verified partial candidate files copied under "
            f"reference/partial-outputs/{attempt.origin}: {listed}"
        )
    return (
        f"{base}\n\n"
        f"{CONTINUATION_BRIEF_MARKER}\n"
        f"The previous worker on route '{context.prior_route}' stopped with failure "
        f"category '{context.failure_category}'. The task, staged inputs, and approved "
        "outputs are unchanged and were revalidated against the original source baseline.\n"
        "Earlier attempts in this chain, oldest first:\n"
        + "\n".join(lines)
        + "\nEach attempt keeps its own labelled directory, so material from different "
        "providers is never mixed. All of it is listed with hashes in "
        "control/continuation.json.\n"
        "Treat that material as reference only. It is not completed, reviewed, or approved "
        "work. Re-derive every approved output from control/brief.txt and the staged inputs, "
        "reuse prior partial progress only where you independently verify it, and do not "
        "replay a failed attempt. Produce the complete approved outputs yourself.\n"
    )


def _default_continuation_stage_root(request: DispatchRequest, route: str) -> Path:
    """Name a fresh sibling stage for one transition, never reusing a stage."""

    root = Path(request.stage_root)
    base = root.name.split("-continuation-")[0]
    return root.parent / f"{base}-continuation-{route}"


def plan_continuation_request(
    config: DelegationConfig,
    request: DispatchRequest,
    worker_receipt: Mapping[str, Any],
    *,
    stage_root: Path | None = None,
) -> DispatchRequest:
    """Build the next request for one eligible configured transition.

    The result keeps the ORIGINAL source root, input inventory, and approved
    outputs, so its later review and reviewed application reach the original
    source tree rather than any staged snapshot. Its lineage accumulates: every
    earlier attempt stays recorded with its own origin label and hashes.
    """

    category, reason = evaluate_continuation_eligibility(config, worker_receipt)
    if category is None:
        raise BoundaryError(f"continuation is not eligible: {reason}")
    route = str(worker_receipt["route"])
    next_route, _ = config.continuation_edge(route)
    if next_route is None:
        raise BoundaryError("continuation is not eligible: route_has_no_configured_continuation_edge")
    layout = _layout_from_worker_receipt(worker_receipt)
    partials, output_errors = _list_stage_outputs(layout)
    partial_manifest, manifest_errors = _output_manifest(layout, partials)
    if output_errors or manifest_errors:
        raise BoundaryError("partial staged outputs are not verifiable")
    if partial_manifest != {
        path: digest
        for path, digest in dict(worker_receipt.get("output_manifest", {})).items()
    }:
        raise BoundaryError("partial staged outputs changed after the failed receipt")
    prior_attempts: tuple[ContinuationAttempt, ...] = ()
    lineage = worker_receipt.get("continuation")
    if lineage is not None:
        prior_attempts = ContinuationContext.from_manifest(lineage).attempts
    attempt = ContinuationAttempt(
        route=route,
        failure_category=category,
        stage_root=str(layout.root),
        receipt_digest=receipt_digest(worker_receipt),
        brief_digest=str(worker_receipt["brief_digest"]),
        origin=continuation_attempt_origin(len(prior_attempts) + 1, route),
        partial_manifest=partial_manifest,
    )
    context = ContinuationContext(
        failure_category=category,
        prior_route=attempt.route,
        prior_stage_root=attempt.stage_root,
        prior_receipt_digest=attempt.receipt_digest,
        prior_brief_digest=attempt.brief_digest,
        input_baseline=dict(layout.input_manifest),
        partial_manifest=dict(partial_manifest),
        attempts=(*prior_attempts, attempt),
    )
    return replace(
        request,
        route=next_route,
        stage_root=(
            Path(stage_root)
            if stage_root is not None
            else _default_continuation_stage_root(request, next_route)
        ),
        brief=_continuation_brief(request.brief, context),
        continuation=context,
    )


def run_staged_worker_with_continuation(
    config: DelegationConfig,
    request: DispatchRequest,
    *,
    runner: Runner = subprocess.run,
    controller_runner: Runner = subprocess.run,
    environment: Mapping[str, str] | None = None,
    dry_run: bool = False,
    ledger_dir: Path | None = None,
    dispatch_id: str | None = None,
    continuation_stage_root: Path | None = None,
    continuation_dispatch_id: str | None = None,
) -> dict[str, Any]:
    """Run one staged worker and follow the configured finite continuation chain.

    Each configured edge fires at most once and only on its own trigger: heavy
    capacity exhaustion continues on the exhaustion fallback, and a verified
    configured-provider insufficient-balance failure continues on the terminal
    balance fallback. Every transition is a complete fresh dispatch with a new
    stage and a new fresh highest controller plan, and each earlier stage, its
    failed receipt, and its verified partial outputs are preserved untouched.
    An ordinary successful worker stops the chain immediately, a route is never
    revisited, and the terminal fallback ends it.
    """

    primary = run_staged_worker(
        config,
        request,
        runner=runner,
        controller_runner=controller_runner,
        environment=environment,
        dry_run=dry_run,
        ledger_dir=ledger_dir,
        dispatch_id=dispatch_id,
    )

    def new_record() -> dict[str, Any]:
        return {
            "attempted": False,
            "route": None,
            "failure_category": None,
            "reason": "not_evaluated",
            "stage_root": None,
            "preserved_partial_manifest": {},
            "receipt": None,
        }

    outcome: dict[str, Any] = {
        "schema_version": 1,
        "kind": "dispatch_outcome",
        "route": request.route,
        "status": primary["status"],
        "effective_result": "primary",
        "primary": primary,
        "continuations": [],
        "continuation": new_record(),
    }
    if dry_run or request.continuation is not None:
        outcome["continuation"]["reason"] = "continuation_not_evaluated_for_this_request"
        outcome["continuations"] = [outcome["continuation"]]
        return outcome

    records: list[dict[str, Any]] = []
    current_request = request
    current_receipt = primary
    # One transition per configured edge at most; the route-repeat rule already
    # makes the chain finite, and this bound documents it.
    for transition in range(len(config.workers)):
        record = new_record()
        category, reason = evaluate_continuation_eligibility(config, current_receipt)
        record["failure_category"] = category
        record["reason"] = reason
        if category is None:
            records.append(record)
            break
        try:
            continuation_request = plan_continuation_request(
                config,
                current_request,
                current_receipt,
                stage_root=continuation_stage_root if transition == 0 else None,
            )
        except BoundaryError as exc:
            record["reason"] = f"continuation_blocked:{exc}"
            records.append(record)
            break
        assert continuation_request.continuation is not None
        record.update(
            {
                "attempted": True,
                "route": continuation_request.route,
                "stage_root": str(continuation_request.stage_root),
                "preserved_partial_manifest": dict(
                    continuation_request.continuation.partial_manifest
                ),
            }
        )
        continuation_receipt = run_staged_worker(
            config,
            continuation_request,
            runner=runner,
            controller_runner=controller_runner,
            environment=environment,
            ledger_dir=ledger_dir,
            dispatch_id=continuation_dispatch_id if transition == 0 else None,
        )
        record["receipt"] = continuation_receipt
        records.append(record)
        outcome["status"] = continuation_receipt["status"]
        outcome["effective_result"] = "continuation"
        current_request = continuation_request
        current_receipt = continuation_receipt
        if continuation_receipt["status"] != "failed":
            break
    else:
        records.append({**new_record(), "reason": "continuation_chain_limit_reached"})

    outcome["continuations"] = records
    attempted = [record for record in records if record["attempted"]]
    outcome["continuation"] = attempted[-1] if attempted else records[-1]
    return outcome


def effective_worker_receipt(outcome: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the final actually executed receipt for review and application.

    A skipped, blocked, or never-evaluated transition has no receipt, so it can
    never look executed; only a worker that really ran can be reviewed.
    """

    records = outcome.get("continuations")
    if isinstance(records, Sequence) and not isinstance(records, (str, bytes)):
        for record in reversed(list(records)):
            if isinstance(record, Mapping) and isinstance(record.get("receipt"), Mapping):
                return record["receipt"]
    continuation = outcome.get("continuation")
    if isinstance(continuation, Mapping) and isinstance(continuation.get("receipt"), Mapping):
        return continuation["receipt"]
    primary = outcome.get("primary")
    if not isinstance(primary, Mapping):
        raise BoundaryError("dispatch outcome has no worker receipt")
    return primary


def request_from_receipt(
    config: DelegationConfig,
    worker_receipt: Mapping[str, Any],
    *,
    timeout_seconds: int | None = None,
) -> DispatchRequest:
    """Rebuild the effective review/apply request from one worker receipt.

    The reconstructed request always targets the receipt's original source root
    and original input inventory, including for a continuation receipt, so a
    fallback result can never be applied to a temporary input snapshot.
    """

    layout = _layout_from_worker_receipt(worker_receipt)
    brief_path = _assert_no_symlink_components(
        layout.control, Path("brief.txt"), leaf_must_exist=True
    )
    try:
        brief_bytes = brief_path.read_bytes()
    except OSError as exc:
        raise BoundaryError("controller brief is unavailable") from exc
    if hashlib.sha256(brief_bytes).hexdigest() != layout.brief_digest:
        raise BoundaryError("controller brief does not match the worker receipt")
    continuation = None
    controller_receipt = worker_receipt.get("controller_receipt")
    if isinstance(controller_receipt, Mapping):
        request_manifest = controller_receipt.get("request_manifest")
        if isinstance(request_manifest, Mapping) and isinstance(
            request_manifest.get("continuation"), Mapping
        ):
            continuation = ContinuationContext.from_manifest(request_manifest["continuation"])
    if continuation is not None and worker_receipt.get("continuation") != continuation.to_manifest():
        raise BoundaryError("worker receipt continuation lineage does not match its controller plan")
    return DispatchRequest(
        route=str(worker_receipt["route"]),
        source_root=layout.source_root,
        stage_root=layout.root,
        inputs=tuple(sorted(layout.input_manifest)),
        approved_outputs=tuple(layout.approved_outputs),
        brief=brief_bytes.decode("utf-8"),
        timeout_seconds=_bounded_timeout(config, timeout_seconds, label="timeout_seconds"),
        workflow_selection=layout.workflow_selection,
        controller_host=str(worker_receipt["controller_host"]),
        continuation=continuation,
    )


def receipt_digest(receipt: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(receipt), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _persist_worker_receipt(layout: StageLayout, receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Persist one terminal worker receipt without embedding its own digest.

    The digest remains an external, canonical hash of the receipt object. This
    avoids a self-referential receipt field while giving later hops a stable
    snapshot they can reload and compare with their recorded digest.
    """

    path = _assert_no_symlink_components(
        layout.control,
        Path("worker-receipt.json"),
        leaf_must_exist=False,
    )
    encoded = json.dumps(dict(receipt), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    try:
        path.write_text(encoded, encoding="utf-8")
        restored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BoundaryError("worker receipt persistence failed") from exc
    if not isinstance(restored, Mapping) or receipt_digest(restored) != receipt_digest(receipt):
        raise BoundaryError("worker receipt persistence did not preserve its digest")
    return dict(receipt)


def _load_persisted_worker_receipt(
    control: Path,
    expected_digest: Any,
) -> dict[str, Any]:
    """Reload the hash-bound worker snapshot for a prior or final attempt."""

    if not _is_sha256_digest(expected_digest):
        raise BoundaryError("recorded worker receipt digest is invalid")
    if control.is_symlink() or not control.is_dir():
        raise BoundaryError("worker receipt control directory is unavailable")
    path = _assert_no_symlink_components(
        control,
        Path("worker-receipt.json"),
        leaf_must_exist=True,
    )
    try:
        restored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BoundaryError("persisted worker receipt is unavailable") from exc
    if not isinstance(restored, Mapping):
        raise BoundaryError("persisted worker receipt is invalid")
    snapshot = dict(restored)
    if receipt_digest(snapshot) != expected_digest:
        raise BoundaryError("persisted worker receipt changed after execution")
    return snapshot


def _controller_request_bytes(request_manifest: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(request_manifest), indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_controller_request(
    layout: StageLayout,
    request_manifest: Mapping[str, Any],
) -> str:
    """Persist the controller's exact request bytes before it can plan."""

    path = _assert_no_symlink_components(
        layout.control,
        Path("controller-request.json"),
        leaf_must_exist=False,
    )
    encoded = _controller_request_bytes(request_manifest)
    path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def _verify_controller_request(
    layout: StageLayout,
    request_manifest: Mapping[str, Any],
) -> str:
    """Reload and hash-check the request bound by the controller receipt."""

    path = _assert_no_symlink_components(
        layout.control,
        Path("controller-request.json"),
        leaf_must_exist=True,
    )
    expected = _controller_request_bytes(request_manifest)
    try:
        actual = path.read_bytes()
        parsed = json.loads(actual)
    except (OSError, json.JSONDecodeError) as exc:
        raise BoundaryError("controller request is unavailable") from exc
    if (
        not isinstance(parsed, Mapping)
        or dict(parsed) != dict(request_manifest)
        or hashlib.sha256(actual).hexdigest() != hashlib.sha256(expected).hexdigest()
    ):
        raise BoundaryError("controller request changed after planning")
    return hashlib.sha256(actual).hexdigest()


def _controller_failure_diagnostic(returncode: int, stdout: Any, stderr: Any) -> str:
    """Classify a controller launch failure without retaining process output."""

    text = "\n".join(
        value for value in (stdout, stderr) if isinstance(value, str)
    ).lower()
    if "requires a newer version of codex" in text:
        return "model_requires_newer_codex"
    if returncode != 0:
        return "controller_exit_nonzero"
    return "invalid_controller_outcome"


def _valid_controller_outcome(
    outcome: Mapping[str, Any] | None,
    request_manifest: Mapping[str, Any],
) -> list[str]:
    """Validate the fresh controller's explicit plan and route decision."""

    if not isinstance(outcome, Mapping):
        return ["missing_or_invalid_controller_outcome"]
    errors: list[str] = []
    if outcome.get("status") not in {"approved", "rejected", "needs_clarification", "failed"}:
        errors.append("invalid_controller_outcome_status")
    # A controller can reject the proposed route, but it cannot silently choose
    # a different one. A parent must issue a concrete revised request instead.
    if outcome.get("route") != request_manifest.get("route"):
        errors.append("controller_outcome_route_mismatch")
    if outcome.get("request_manifest") != dict(request_manifest):
        errors.append("controller_outcome_request_manifest_mismatch")
    if outcome.get("fresh_process") is not True:
        errors.append("controller_outcome_not_fresh")
    for field, require_nonempty in (
        ("plan_steps", True),
        ("evidence", True),
        ("limitations", False),
    ):
        value = outcome.get(field)
        if (
            not isinstance(value, list)
            or (require_nonempty and not value)
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            errors.append(f"invalid_controller_outcome_{field}")
    return errors


def run_controller_planner(
    config: DelegationConfig,
    request: DispatchRequest,
    layout: StageLayout,
    *,
    runner: Runner = subprocess.run,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """Run the required fresh highest-binding planning process before a worker.

    This process has reviewer-grade read-only permissions. Its plan must bind
    the exact proposed route and staged request context, so it cannot act as a
    mere host-model marker or select a route on the parent's behalf.
    """

    _workflow_selection_context(request.workflow_selection)
    timeout = _bounded_timeout(config, timeout_seconds, label="timeout_seconds")
    binding = config.resolve_host_binding(request.controller_host, "controller")
    request_manifest = _controller_request_manifest(config, request, layout)
    _write_controller_request(layout, request_manifest)
    command = build_controller_command(
        config,
        request.controller_host,
        layout,
        request_manifest,
        workflow_selection=layout.workflow_selection,
    )
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "kind": "controller",
        "source": "subprocess",
        "host": request.controller_host,
        "binding": binding.name,
        "requested_model": binding.model,
        "requested_effort": binding.effort,
        "actual_model": "unobserved",
        "actual_effort": "unobserved",
        "sandbox": binding.sandbox,
        "stage_root": str(layout.root),
        "route": request.route,
        "workflow_selection": layout.workflow_selection,
        "request_manifest": request_manifest,
        "status": "failed",
        "diagnostic": "not_started",
        "fresh_process": True,
        "plan_steps": [],
        "limitations": [],
        "process": {
            "launcher": binding.launcher,
            "exit_code": 127,
            "command_digest": hashlib.sha256(
                json.dumps(command, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "output_digest": hashlib.sha256(b"").hexdigest(),
        },
        "evidence": ["fresh controller process not yet completed"],
    }
    try:
        completed = runner(
            command,
            cwd=str(layout.workspace),
            env=_reviewer_environment(environment),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        receipt["process"]["exit_code"] = 124
        receipt["diagnostic"] = "controller_timeout"
        receipt["evidence"] = ["controller diagnostic: controller_timeout"]
        return receipt
    except OSError:
        receipt["diagnostic"] = "controller_launch_failed"
        receipt["evidence"] = ["controller diagnostic: controller_launch_failed"]
        return receipt

    stdout = completed.stdout if isinstance(completed.stdout, str) else ""
    stderr = completed.stderr if isinstance(completed.stderr, str) else ""
    receipt["process"]["exit_code"] = completed.returncode
    receipt["process"]["output_digest"] = hashlib.sha256(
        (stdout + "\0" + stderr).encode("utf-8")
    ).hexdigest()
    outcome = _object_from_reviewer_stdout(stdout)
    outcome_errors = _valid_controller_outcome(outcome, request_manifest)
    if completed.returncode != 0:
        outcome_errors.append("controller_exit_nonzero")
    if outcome_errors:
        diagnostic = _controller_failure_diagnostic(completed.returncode, stdout, stderr)
        receipt["diagnostic"] = diagnostic
        receipt["evidence"] = [f"controller diagnostic: {diagnostic}"]
        return receipt
    assert outcome is not None
    receipt["status"] = outcome["status"]
    receipt["diagnostic"] = "none"
    receipt["plan_steps"] = list(outcome["plan_steps"])
    receipt["limitations"] = list(outcome["limitations"])
    receipt["evidence"] = list(outcome["evidence"])
    return receipt


def _controller_plan_payload(
    controller_receipt: Mapping[str, Any],
    request_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "controller_receipt_digest": receipt_digest(controller_receipt),
        "controller_host": controller_receipt["host"],
        "route": controller_receipt["route"],
        "request_manifest": dict(request_manifest),
        "plan_steps": list(controller_receipt["plan_steps"]),
        "evidence": list(controller_receipt["evidence"]),
        "limitations": list(controller_receipt["limitations"]),
    }


def _write_controller_plan(
    layout: StageLayout,
    controller_receipt: Mapping[str, Any],
    request_manifest: Mapping[str, Any],
) -> str:
    if controller_receipt.get("status") != "approved":
        raise BoundaryError("a controller plan must be approved before it is written")
    path = _assert_no_symlink_components(
        layout.control,
        Path("controller-plan.json"),
        leaf_must_exist=False,
    )
    payload = _controller_plan_payload(controller_receipt, request_manifest)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(encoded, encoding="utf-8")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _verify_controller_plan(
    layout: StageLayout,
    controller_receipt: Mapping[str, Any],
    request_manifest: Mapping[str, Any],
    expected_digest: Any,
) -> str:
    """Ensure the exact controller plan bytes still bind the receipt."""

    if not _is_sha256_digest(expected_digest):
        raise BoundaryError("controller plan digest is invalid")
    path = _assert_no_symlink_components(
        layout.control,
        Path("controller-plan.json"),
        leaf_must_exist=True,
    )
    try:
        content = path.read_bytes()
        payload = json.loads(content)
    except (OSError, json.JSONDecodeError) as exc:
        raise BoundaryError("controller plan is unavailable") from exc
    digest = hashlib.sha256(content).hexdigest()
    if digest != expected_digest:
        raise BoundaryError("controller plan changed after controller approval")
    if not isinstance(payload, Mapping) or dict(payload) != _controller_plan_payload(
        controller_receipt, request_manifest
    ):
        raise BoundaryError("controller plan does not bind the controller receipt")
    return digest


def _layout_from_worker_receipt(worker_receipt: Mapping[str, Any]) -> StageLayout:
    source_root = Path(str(worker_receipt["source_root"]))
    stage_root = Path(str(worker_receipt["stage_root"]))
    if source_root.is_symlink() or not source_root.is_dir():
        raise BoundaryError("worker source root is unavailable")
    if stage_root.is_symlink() or not stage_root.is_dir():
        raise BoundaryError("worker stage root is unavailable")
    source_root = source_root.resolve(strict=True)
    stage_root = stage_root.resolve(strict=True)
    return StageLayout(
        root=stage_root,
        workspace=stage_root / "workspace",
        inputs=stage_root / "inputs",
        outputs=stage_root / "outputs",
        control=stage_root / "workspace" / "control",
        source_root=source_root,
        approved_outputs=tuple(str(item) for item in worker_receipt["approved_outputs"]),
        input_manifest={
            str(path): str(digest)
            for path, digest in dict(worker_receipt["input_manifest"]).items()
        },
        brief_digest=str(worker_receipt["brief_digest"]),
        workflow_selection=str(worker_receipt["workflow_selection"]),
    )


def _new_empty_directory(path: Path, label: str) -> Path:
    requested = Path(path)
    if requested.is_symlink() or not requested.parent.is_dir():
        raise BoundaryError(f"{label} must have a real existing parent")
    if requested.exists():
        if not requested.is_dir() or any(requested.iterdir()):
            raise BoundaryError(f"{label} must be new or empty")
    else:
        requested.mkdir(mode=0o700)
    resolved = requested.resolve(strict=True)
    if resolved.is_symlink() or not resolved.is_dir():
        raise BoundaryError(f"{label} is not a real directory")
    return resolved


def prepare_review_package(
    config: DelegationConfig,
    worker_receipt: Mapping[str, Any],
    review_root: Path,
) -> ReviewPackage:
    """Copy reviewed outputs plus the hash-bound controller task context."""

    if validate_receipt(config, worker_receipt):
        raise BoundaryError("worker receipt is invalid")
    if worker_receipt.get("status") != "completed":
        raise BoundaryError("only completed staged output can be reviewed")
    layout = _layout_from_worker_receipt(worker_receipt)
    _verify_persisted_worker_receipt(config, layout, worker_receipt)
    _verify_source_baseline(layout)
    _verify_prior_attempts(config, layout, _lineage_attempts(worker_receipt))
    actual_outputs, output_errors = _list_stage_outputs(layout)
    actual_manifest, manifest_errors = _output_manifest(layout, actual_outputs)
    expected_outputs = list(worker_receipt["changed_outputs"])
    expected_manifest = dict(worker_receipt["output_manifest"])
    if (
        output_errors
        or manifest_errors
        or sorted(actual_outputs) != sorted(expected_outputs)
        or actual_manifest != expected_manifest
    ):
        raise BoundaryError("staged outputs changed before review packaging")

    staged_inputs, input_errors = _directory_manifest(layout.inputs, label="staged_input")
    if input_errors or staged_inputs != dict(layout.input_manifest):
        raise BoundaryError("staged inputs changed before review packaging")
    if layout.control.is_symlink() or not layout.control.is_dir():
        raise BoundaryError("controller brief directory is unavailable")
    brief_path = _assert_no_symlink_components(
        layout.control,
        Path("brief.txt"),
        leaf_must_exist=True,
    )
    try:
        actual_brief_digest = hashlib.sha256(brief_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise BoundaryError("controller brief is unavailable") from exc
    if actual_brief_digest != layout.brief_digest:
        raise BoundaryError("controller brief changed before review packaging")

    controller_receipt = worker_receipt.get("controller_receipt")
    if not isinstance(controller_receipt, Mapping):
        raise BoundaryError("worker receipt has no controller receipt")
    request_manifest = controller_receipt.get("request_manifest")
    if not isinstance(request_manifest, Mapping):
        raise BoundaryError("controller request manifest is unavailable")
    _verify_worker_controller_context(config, layout, worker_receipt)
    controller_receipt_digest = receipt_digest(controller_receipt)
    if worker_receipt.get("controller_receipt_digest") != controller_receipt_digest:
        raise BoundaryError("worker receipt controller digest does not match controller receipt")
    controller_plan_digest = _verify_controller_plan(
        layout,
        controller_receipt,
        request_manifest,
        worker_receipt.get("controller_plan_digest"),
    )

    root = _new_empty_directory(review_root, "review root")
    candidate_outputs = root / "candidate-outputs"
    candidate_outputs.mkdir()
    for value in actual_outputs:
        relative = _relative_path(value, "reviewed output")
        source = _assert_no_symlink_components(layout.outputs, relative, leaf_must_exist=True)
        destination = candidate_outputs / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)

    baseline_inputs = root / "baseline-inputs"
    baseline_inputs.mkdir()
    for value in sorted(staged_inputs):
        relative = _relative_path(value, "reviewed input")
        source = _assert_no_symlink_components(layout.inputs, relative, leaf_must_exist=True)
        destination = baseline_inputs / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
    shutil.copy2(brief_path, root / "controller-brief.txt", follow_symlinks=False)
    controller_plan = _assert_no_symlink_components(
        layout.control,
        Path("controller-plan.json"),
        leaf_must_exist=True,
    )
    shutil.copy2(controller_plan, root / "controller-plan.json", follow_symlinks=False)
    (root / "controller-receipt.json").write_text(
        json.dumps(dict(controller_receipt), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    worker_digest = receipt_digest(worker_receipt)
    evidence = {
        "worker_receipt_digest": worker_digest,
        "output_manifest": actual_manifest,
        "controller_brief_digest": actual_brief_digest,
        "staged_input_manifest": staged_inputs,
        "workflow_selection": layout.workflow_selection,
        "controller_host": worker_receipt["controller_host"],
        "controller_receipt_digest": controller_receipt_digest,
        "controller_plan_digest": controller_plan_digest,
        "continuation": worker_receipt.get("continuation"),
        "worker": {
            key: worker_receipt[key]
            for key in (
                "route",
                "binding",
                "launcher",
                "permission_profile",
                "write_boundary",
                "requested_model",
                "requested_effort",
                "actual_model",
                "actual_effort",
                "observed_model_usage",
                "status",
                "changed_outputs",
                "child_exit_code",
                "failure_category",
                "errors",
                "evidence",
                "self_tests",
                "limitations",
            )
        },
    }
    evidence_path = root / "review-evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (root / "empty-mcp.json").write_text('{"mcpServers":{}}\n', encoding="utf-8")
    return ReviewPackage(
        root=root,
        evidence_path=evidence_path,
        candidate_outputs=candidate_outputs,
        worker_digest=worker_digest,
        output_manifest=actual_manifest,
        input_manifest=staged_inputs,
        brief_digest=actual_brief_digest,
        workflow_selection=layout.workflow_selection,
        controller_host=str(worker_receipt["controller_host"]),
        controller_receipt_digest=controller_receipt_digest,
        controller_plan_digest=controller_plan_digest,
    )


def _reviewer_environment(environment: Mapping[str, str] | None) -> dict[str, str]:
    source = dict(os.environ if environment is None else environment)
    return {
        key: source[key]
        for key in CODEX_ENVIRONMENT_KEYS
        if isinstance(source.get(key), str) and source[key]
    }


def _outcome_from_text(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) and "status" in parsed else None


def _outcome_from_json_object(value: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Extract a review decision from direct or JSON-event CLI output."""

    if "status" in value:
        return value
    for key in ("result", "output_text", "text", "content"):
        outcome = _outcome_from_text(value.get(key))
        if outcome is not None:
            return outcome
    for wrapper_key in ("item", "message"):
        wrapper = value.get(wrapper_key)
        if not isinstance(wrapper, Mapping):
            continue
        # Codex --json commonly wraps the last assistant answer as an
        # item.completed agent_message. Claude JSON can use a result field.
        for key in ("result", "output_text", "text", "content"):
            outcome = _outcome_from_text(wrapper.get(key))
            if outcome is not None:
                return outcome
    return None


def _object_from_reviewer_stdout(stdout: Any) -> Mapping[str, Any] | None:
    if not isinstance(stdout, str) or not stdout.strip():
        return None
    candidates = [stdout.strip(), *reversed([line for line in stdout.splitlines() if line.strip()])]
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, Mapping):
            outcome = _outcome_from_json_object(parsed)
            if outcome is not None:
                return outcome
    return None


def _reviewer_failure_diagnostic(returncode: int, stdout: Any, stderr: Any) -> str:
    """Classify a bounded operational failure without retaining process output."""

    text = "\n".join(
        value for value in (stdout, stderr) if isinstance(value, str)
    ).lower()
    if "requires a newer version of codex" in text:
        return "model_requires_newer_codex"
    if returncode != 0:
        return "reviewer_exit_nonzero"
    return "invalid_reviewer_outcome"


def _valid_reviewer_outcome(outcome: Mapping[str, Any] | None, package: ReviewPackage) -> list[str]:
    if not isinstance(outcome, Mapping):
        return ["missing_or_invalid_reviewer_outcome"]
    errors: list[str] = []
    if outcome.get("status") not in {"approved", "rejected", "failed"}:
        errors.append("invalid_reviewer_outcome_status")
    if outcome.get("reviewed_worker_digest") != package.worker_digest:
        errors.append("reviewer_outcome_worker_digest_mismatch")
    if outcome.get("reviewed_output_manifest") != dict(package.output_manifest):
        errors.append("reviewer_outcome_manifest_mismatch")
    if outcome.get("reviewed_brief_digest") != package.brief_digest:
        errors.append("reviewer_outcome_brief_digest_mismatch")
    if outcome.get("reviewed_input_manifest") != dict(package.input_manifest):
        errors.append("reviewer_outcome_input_manifest_mismatch")
    if outcome.get("reviewed_workflow_selection") != package.workflow_selection:
        errors.append("reviewer_outcome_workflow_selection_mismatch")
    if outcome.get("reviewed_controller_receipt_digest") != package.controller_receipt_digest:
        errors.append("reviewer_outcome_controller_receipt_digest_mismatch")
    if outcome.get("reviewed_controller_plan_digest") != package.controller_plan_digest:
        errors.append("reviewer_outcome_controller_plan_digest_mismatch")
    if outcome.get("fresh_process") is not True:
        errors.append("reviewer_outcome_not_fresh")
    evidence = outcome.get("evidence")
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(item, str) and item.strip() for item in evidence
    ):
        errors.append("invalid_reviewer_outcome_evidence")
    return errors


def run_independent_reviewer(
    config: DelegationConfig,
    *,
    host: str,
    worker_receipt: Mapping[str, Any],
    review_root: Path,
    runner: Runner = subprocess.run,
    environment: Mapping[str, str] | None = None,
    timeout_seconds: int | None = None,
    workflow_selection: str | None = None,
) -> dict[str, Any]:
    """Run a fresh configured reviewer and bind its process result to the candidate."""

    _workflow_selection_context(workflow_selection)
    selection = str(workflow_selection)
    timeout = _bounded_timeout(config, timeout_seconds, label="timeout_seconds")
    if host != worker_receipt.get("controller_host"):
        raise BoundaryError("independent reviewer host must match the worker controller host")
    package = prepare_review_package(config, worker_receipt, review_root)
    if package.workflow_selection != selection:
        raise BoundaryError("review workflow selection does not match the worker receipt")
    binding = config.resolve_host_binding(host, "reviewer")
    command = build_reviewer_command(
        config,
        host,
        package.root,
        workflow_selection=selection,
    )
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "kind": "reviewer",
        "source": "subprocess",
        "host": host,
        "binding": binding.name,
        "requested_model": binding.model,
        "requested_effort": binding.effort,
        "actual_model": "unobserved",
        "actual_effort": "unobserved",
        "sandbox": binding.sandbox,
        "stage_root": str(_layout_from_worker_receipt(worker_receipt).root),
        "review_package_root": str(package.root),
        "status": "failed",
        "diagnostic": "not_started",
        "fresh_process": True,
        "reviewed_worker_digest": package.worker_digest,
        "reviewed_output_manifest": dict(package.output_manifest),
        "reviewed_brief_digest": package.brief_digest,
        "reviewed_input_manifest": dict(package.input_manifest),
        "workflow_selection": selection,
        "reviewed_workflow_selection": package.workflow_selection,
        "controller_host": package.controller_host,
        "reviewed_controller_receipt_digest": package.controller_receipt_digest,
        "reviewed_controller_plan_digest": package.controller_plan_digest,
        "process": {
            "launcher": binding.launcher,
            "exit_code": 127,
            "command_digest": hashlib.sha256(
                json.dumps(command, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "output_digest": hashlib.sha256(b"").hexdigest(),
        },
        "evidence": ["fresh reviewer process not yet completed"],
    }
    try:
        completed = runner(
            command,
            cwd=str(package.root),
            env=_reviewer_environment(environment),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        receipt["process"]["exit_code"] = 124
        receipt["diagnostic"] = "reviewer_timeout"
        receipt["evidence"] = ["reviewer diagnostic: reviewer_timeout"]
        return receipt
    except OSError:
        receipt["diagnostic"] = "reviewer_launch_failed"
        receipt["evidence"] = ["reviewer diagnostic: reviewer_launch_failed"]
        return receipt

    stdout = completed.stdout if isinstance(completed.stdout, str) else ""
    stderr = completed.stderr if isinstance(completed.stderr, str) else ""
    receipt["process"]["exit_code"] = completed.returncode
    receipt["process"]["output_digest"] = hashlib.sha256(
        (stdout + "\0" + stderr).encode("utf-8")
    ).hexdigest()
    outcome = _object_from_reviewer_stdout(stdout)
    outcome_errors = _valid_reviewer_outcome(outcome, package)
    if completed.returncode != 0:
        outcome_errors.append("reviewer_exit_nonzero")
    if outcome_errors:
        diagnostic = _reviewer_failure_diagnostic(completed.returncode, stdout, stderr)
        receipt["diagnostic"] = diagnostic
        receipt["evidence"] = [f"reviewer diagnostic: {diagnostic}"]
        return receipt
    assert outcome is not None
    receipt["status"] = outcome["status"]
    receipt["diagnostic"] = "none"
    receipt["evidence"] = list(outcome["evidence"])
    return receipt


def _require_string_list(value: Any, label: str, errors: list[str]) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        errors.append(label)
        return None
    return list(value)


def _is_sha256_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_hash_manifest(
    value: Any,
    label: str,
    errors: list[str],
) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        errors.append(label)
        return None
    manifest = dict(value)
    if not all(
        isinstance(path, str)
        and _is_sha256_digest(digest)
        for path, digest in manifest.items()
    ):
        errors.append(label)
        return None
    try:
        for path in manifest:
            _relative_path(path, "manifest path")
    except BoundaryError:
        errors.append(label)
        return None
    return {str(path): str(digest) for path, digest in manifest.items()}


def _continuation_lineage_routes_are_configured(
    config: DelegationConfig,
    continuation: Mapping[str, Any],
) -> bool:
    """Every route named anywhere in a lineage must be a configured worker."""

    routes = [continuation.get("prior_route")]
    for attempt in continuation.get("attempts", []):
        if isinstance(attempt, Mapping):
            routes.append(attempt.get("route"))
    return all(route in config.workers for route in routes)


def _validate_controller_request_manifest(
    config: DelegationConfig,
    value: Any,
    errors: list[str],
) -> dict[str, Any] | None:
    """Validate the controller's bound request context without trusting a caller."""

    if not isinstance(value, Mapping):
        errors.append("invalid_controller_request_manifest")
        return None
    manifest = dict(value)
    expected_keys = {
        "config_digest",
        "controller_host",
        "route",
        "workflow_selection",
        "brief_digest",
        "input_manifest",
        "approved_outputs",
    }
    # A continuation dispatch adds exactly one optional hash-bound lineage block.
    optional_keys = {"continuation"}
    if not expected_keys <= set(manifest) <= expected_keys | optional_keys:
        errors.append("invalid_controller_request_manifest")
        return None
    if manifest.get("config_digest") != config.config_digest:
        errors.append("controller_request_config_digest_mismatch")
    host = manifest.get("controller_host")
    if not isinstance(host, str) or host not in config.hosts:
        errors.append("invalid_controller_request_host")
    route = manifest.get("route")
    if not isinstance(route, str) or route not in config.workers:
        errors.append("invalid_controller_request_route")
    if manifest.get("workflow_selection") not in WORKFLOW_SELECTIONS:
        errors.append("invalid_controller_request_workflow_selection")
    if not _is_sha256_digest(manifest.get("brief_digest")):
        errors.append("invalid_controller_request_brief_digest")
    input_manifest = _require_hash_manifest(
        manifest.get("input_manifest"),
        "invalid_controller_request_input_manifest",
        errors,
    )
    if input_manifest is not None and not input_manifest:
        errors.append("invalid_controller_request_input_manifest")
    approved = _require_string_list(
        manifest.get("approved_outputs"),
        "invalid_controller_request_approved_outputs",
        errors,
    )
    if approved is not None:
        try:
            for output in approved:
                _relative_path(output, "controller approved output")
        except BoundaryError:
            errors.append("invalid_controller_request_approved_outputs")
    if "continuation" in manifest:
        continuation_errors = continuation_manifest_errors(manifest.get("continuation"))
        if continuation_errors:
            errors.extend(f"controller_request_{error}" for error in continuation_errors)
        elif not _continuation_lineage_routes_are_configured(config, manifest["continuation"]):
            errors.append("controller_request_invalid_continuation_prior_route")
    return manifest


def validate_controller_receipt(
    config: DelegationConfig,
    receipt: Mapping[str, Any],
) -> list[str]:
    """Validate a subprocess-backed fresh controller planning receipt."""

    if not isinstance(receipt, Mapping):
        return ["invalid_controller_receipt_type"]
    errors: list[str] = []
    if receipt.get("schema_version") != 1:
        errors.append("invalid_controller_schema_version")
    if receipt.get("kind") != "controller":
        errors.append("invalid_controller_receipt_kind")
    if receipt.get("source") != "subprocess":
        errors.append("controller_must_be_subprocess_backed")
    if receipt.get("fresh_process") is not True:
        errors.append("controller_must_be_fresh")
    host = receipt.get("host")
    if not isinstance(host, str) or host not in config.hosts:
        return sorted(set(errors + ["invalid_controller_host"]))
    binding = config.resolve_host_binding(host, "controller")
    if receipt.get("binding") != binding.name:
        errors.append("controller_binding_does_not_match_config")
    if receipt.get("requested_model") != binding.model:
        errors.append("controller_requested_model_does_not_match_config")
    if receipt.get("requested_effort") != binding.effort:
        errors.append("controller_requested_effort_does_not_match_config")
    if receipt.get("sandbox") != binding.sandbox:
        errors.append("controller_sandbox_does_not_match_config")
    for field, expected in (("actual_model", binding.model), ("actual_effort", binding.effort)):
        actual = receipt.get(field)
        if not isinstance(actual, str) or not actual:
            errors.append(f"invalid_controller_{field}")
        elif actual != "unobserved" and actual != expected:
            errors.append(f"controller_{field}_does_not_match_requested_binding")
    if not isinstance(receipt.get("stage_root"), str) or not receipt["stage_root"]:
        errors.append("invalid_controller_stage_root")
    route = receipt.get("route")
    if not isinstance(route, str) or route not in config.workers:
        errors.append("invalid_controller_route")
    if receipt.get("workflow_selection") not in WORKFLOW_SELECTIONS:
        errors.append("invalid_controller_workflow_selection")
    request_manifest = _validate_controller_request_manifest(
        config,
        receipt.get("request_manifest"),
        errors,
    )
    if request_manifest is not None:
        if request_manifest.get("controller_host") != host:
            errors.append("controller_request_host_mismatch")
        if request_manifest.get("route") != route:
            errors.append("controller_request_route_mismatch")
        if request_manifest.get("workflow_selection") != receipt.get("workflow_selection"):
            errors.append("controller_request_workflow_selection_mismatch")
    status = receipt.get("status")
    if status not in {"approved", "rejected", "needs_clarification", "failed"}:
        errors.append("invalid_controller_receipt_status")
    diagnostic = receipt.get("diagnostic")
    if diagnostic not in CONTROLLER_DIAGNOSTICS:
        errors.append("invalid_controller_diagnostic")
    elif status in {"approved", "rejected", "needs_clarification"} and diagnostic != "none":
        errors.append("controller_decision_has_failure_diagnostic")
    process = receipt.get("process")
    if not isinstance(process, Mapping):
        errors.append("invalid_controller_process")
    else:
        if process.get("launcher") != binding.launcher:
            errors.append("controller_process_launcher_mismatch")
        if not isinstance(process.get("exit_code"), int):
            errors.append("invalid_controller_exit_code")
        if not _is_sha256_digest(process.get("command_digest")):
            errors.append("invalid_controller_command_digest")
        if not _is_sha256_digest(process.get("output_digest")):
            errors.append("invalid_controller_output_digest")
        if status in {"approved", "rejected", "needs_clarification"} and process.get("exit_code") != 0:
            errors.append("controller_nonzero_exit_cannot_decide")
    _require_string_list(receipt.get("plan_steps"), "invalid_controller_plan_steps", errors)
    _require_string_list(receipt.get("limitations"), "invalid_controller_limitations", errors)
    _require_string_list(receipt.get("evidence"), "invalid_controller_evidence", errors)
    if status == "approved" and not receipt.get("plan_steps"):
        errors.append("approved_controller_without_plan_steps")
    return sorted(set(errors))


def validate_receipt(config: DelegationConfig, receipt: Mapping[str, Any]) -> list[str]:
    """Validate a worker or reviewer receipt against the current config."""

    if not isinstance(receipt, Mapping):
        return ["invalid_receipt_type"]
    errors: list[str] = []
    if receipt.get("schema_version") != 1:
        errors.append("invalid_schema_version")
    kind = receipt.get("kind")
    if kind == "worker":
        route = receipt.get("route")
        if not isinstance(route, str) or route not in config.workers:
            return sorted(set(errors + ["invalid_worker_route"]))
        binding = config.resolve_worker_binding(route)
        expected_statuses = {"planned", "completed", "blocked", "failed", "needs_clarification"}
        # Each launcher keeps its own enforced boundary vocabulary: the Codex OS
        # permission profile for the stage root, or the restricted Claude
        # file-tool set limited to the exact approved output paths.
        expected_profile, expected_boundary = WORKER_BOUNDARIES.get(binding.launcher, ("", ""))
        if receipt.get("permission_profile") != expected_profile:
            errors.append("invalid_permission_profile")
        if receipt.get("write_boundary") != expected_boundary:
            errors.append("invalid_write_boundary")
        if "launcher" in receipt and receipt.get("launcher") != binding.launcher:
            errors.append("invalid_worker_launcher")
        if not isinstance(receipt.get("stage_root"), str) or not receipt["stage_root"]:
            errors.append("invalid_stage_root")
        if not isinstance(receipt.get("source_root"), str) or not receipt["source_root"]:
            errors.append("invalid_source_root")
        if receipt.get("workflow_selection") not in WORKFLOW_SELECTIONS:
            errors.append("invalid_workflow_selection")
        if "failure_category" in receipt:
            if receipt.get("failure_category") not in FAILURE_CATEGORIES:
                errors.append("invalid_failure_category")
            elif receipt.get("status") == "completed" and receipt.get("failure_category") != "none":
                errors.append("completed_with_failure_category")
        continuation = receipt.get("continuation")
        if continuation is not None:
            continuation_errors = continuation_manifest_errors(continuation)
            if continuation_errors:
                errors.extend(continuation_errors)
            elif not _continuation_lineage_routes_are_configured(config, continuation):
                errors.append("invalid_continuation_prior_route")
        controller_host = receipt.get("controller_host")
        if not isinstance(controller_host, str) or controller_host not in config.hosts:
            errors.append("invalid_controller_host")
        controller_receipt = receipt.get("controller_receipt")
        controller_digest = receipt.get("controller_receipt_digest")
        controller_plan_digest = receipt.get("controller_plan_digest")
        if receipt.get("status") == "completed":
            if not isinstance(controller_receipt, Mapping):
                errors.append("completed_without_controller_receipt")
            else:
                errors.extend(
                    f"worker_{error}"
                    for error in validate_controller_receipt(config, controller_receipt)
                )
                if controller_receipt.get("status") != "approved":
                    errors.append("completed_without_approved_controller_plan")
                if controller_receipt.get("host") != controller_host:
                    errors.append("worker_controller_host_mismatch")
                if controller_receipt.get("route") != route:
                    errors.append("worker_controller_route_mismatch")
                if controller_receipt.get("workflow_selection") != receipt.get("workflow_selection"):
                    errors.append("worker_controller_workflow_mismatch")
                request_manifest = controller_receipt.get("request_manifest")
                if isinstance(request_manifest, Mapping):
                    if request_manifest.get("brief_digest") != receipt.get("brief_digest"):
                        errors.append("worker_controller_brief_mismatch")
                    if request_manifest.get("input_manifest") != receipt.get("input_manifest"):
                        errors.append("worker_controller_inputs_mismatch")
                    if request_manifest.get("approved_outputs") != receipt.get("approved_outputs"):
                        errors.append("worker_controller_outputs_mismatch")
                    if request_manifest.get("config_digest") != config.config_digest:
                        errors.append("worker_controller_config_mismatch")
                    if request_manifest.get("continuation") != receipt.get("continuation"):
                        errors.append("worker_controller_continuation_mismatch")
            if not _is_sha256_digest(controller_digest) or not isinstance(controller_receipt, Mapping):
                errors.append("invalid_controller_receipt_digest")
            elif controller_digest != receipt_digest(controller_receipt):
                errors.append("controller_receipt_digest_mismatch")
            if not _is_sha256_digest(controller_plan_digest):
                errors.append("invalid_controller_plan_digest")
        elif controller_receipt is not None:
            if not isinstance(controller_receipt, Mapping):
                errors.append("invalid_controller_receipt")
            else:
                errors.extend(
                    f"worker_{error}"
                    for error in validate_controller_receipt(config, controller_receipt)
                )
                if controller_digest != receipt_digest(controller_receipt):
                    errors.append("controller_receipt_digest_mismatch")
        input_manifest = _require_hash_manifest(
            receipt.get("input_manifest"),
            "invalid_input_manifest",
            errors,
        )
        if input_manifest is not None and not input_manifest:
            errors.append("invalid_input_manifest")
        if not _is_sha256_digest(receipt.get("brief_digest")):
            errors.append("invalid_brief_digest")
        approved = _require_string_list(receipt.get("approved_outputs"), "invalid_approved_outputs", errors)
        changed = _require_string_list(receipt.get("changed_outputs"), "invalid_changed_outputs", errors)
        if approved is not None:
            try:
                for value in approved:
                    _relative_path(value, "approved output")
            except BoundaryError:
                errors.append("invalid_approved_outputs")
        if approved is not None and changed is not None and not set(changed).issubset(set(approved)):
            errors.append("changed_output_outside_approval")
        manifest = _require_hash_manifest(
            receipt.get("output_manifest"),
            "invalid_output_manifest",
            errors,
        )
        if manifest is not None and changed is not None:
            # A completed receipt must describe exactly its changed outputs. An
            # unfinished attempt may additionally record verified partial files,
            # which a continuation carries forward as reference only.
            if receipt.get("status") == "completed":
                if set(manifest) != set(changed):
                    errors.append("invalid_output_manifest")
            elif not set(changed).issubset(set(manifest)):
                errors.append("invalid_output_manifest")
        if receipt.get("nested_dispatch") != "not_attempted":
            errors.append("nested_dispatch_not_blocked")
        _require_string_list(receipt.get("self_tests"), "invalid_self_tests", errors)
        _require_string_list(receipt.get("limitations"), "invalid_limitations", errors)
    elif kind == "reviewer":
        host = receipt.get("host")
        if not isinstance(host, str) or host not in config.hosts:
            return sorted(set(errors + ["invalid_reviewer_host"]))
        binding = config.resolve_host_binding(host, "reviewer")
        expected_statuses = {"approved", "rejected", "failed"}
        if receipt.get("source") != "subprocess":
            errors.append("reviewer_must_be_subprocess_backed")
        if receipt.get("fresh_process") is not True:
            errors.append("reviewer_must_be_fresh")
        if receipt.get("diagnostic") not in REVIEWER_DIAGNOSTICS:
            errors.append("invalid_reviewer_diagnostic")
        elif receipt.get("status") in {"approved", "rejected"} and receipt.get("diagnostic") != "none":
            errors.append("reviewer_decision_has_failure_diagnostic")
        if not isinstance(receipt.get("stage_root"), str) or not receipt["stage_root"]:
            errors.append("invalid_stage_root")
        if receipt.get("workflow_selection") not in WORKFLOW_SELECTIONS:
            errors.append("invalid_workflow_selection")
        if receipt.get("reviewed_workflow_selection") not in WORKFLOW_SELECTIONS:
            errors.append("invalid_reviewed_workflow_selection")
        elif receipt.get("reviewed_workflow_selection") != receipt.get("workflow_selection"):
            errors.append("reviewed_workflow_selection_mismatch")
        if not isinstance(receipt.get("reviewed_worker_digest"), str) or not receipt["reviewed_worker_digest"]:
            errors.append("invalid_reviewed_worker_digest")
        _require_hash_manifest(
            receipt.get("reviewed_output_manifest"),
            "invalid_reviewed_output_manifest",
            errors,
        )
        _require_hash_manifest(
            receipt.get("reviewed_input_manifest"),
            "invalid_reviewed_input_manifest",
            errors,
        )
        if not _is_sha256_digest(receipt.get("reviewed_brief_digest")):
            errors.append("invalid_reviewed_brief_digest")
        controller_host = receipt.get("controller_host")
        if not isinstance(controller_host, str) or controller_host not in config.hosts:
            errors.append("invalid_reviewer_controller_host")
        if not _is_sha256_digest(receipt.get("reviewed_controller_receipt_digest")):
            errors.append("invalid_reviewed_controller_receipt_digest")
        if not _is_sha256_digest(receipt.get("reviewed_controller_plan_digest")):
            errors.append("invalid_reviewed_controller_plan_digest")
        process = receipt.get("process")
        if not isinstance(process, Mapping):
            errors.append("invalid_reviewer_process")
        else:
            if process.get("launcher") != binding.launcher:
                errors.append("reviewer_process_launcher_mismatch")
            if not isinstance(process.get("exit_code"), int):
                errors.append("invalid_reviewer_exit_code")
            if not isinstance(process.get("command_digest"), str) or len(process["command_digest"]) != 64:
                errors.append("invalid_reviewer_command_digest")
            if not isinstance(process.get("output_digest"), str) or len(process["output_digest"]) != 64:
                errors.append("invalid_reviewer_output_digest")
            if receipt.get("status") in {"approved", "rejected"} and process.get("exit_code") != 0:
                errors.append("reviewer_nonzero_exit_cannot_decide")
    else:
        return sorted(set(errors + ["invalid_receipt_kind"]))

    if receipt.get("binding") != binding.name:
        errors.append("binding_does_not_match_config")
    if receipt.get("requested_model") != binding.model:
        errors.append("requested_model_does_not_match_config")
    if receipt.get("requested_effort") != binding.effort:
        errors.append("requested_effort_does_not_match_config")
    if receipt.get("sandbox") != binding.sandbox:
        errors.append("sandbox_does_not_match_config")
    for field, expected in (("actual_model", binding.model), ("actual_effort", binding.effort)):
        actual = receipt.get(field)
        if not isinstance(actual, str) or not actual:
            errors.append(f"invalid_{field}")
        elif actual != "unobserved" and actual != expected:
            errors.append(f"{field}_does_not_match_requested_binding")
    if receipt.get("status") not in expected_statuses:
        errors.append("invalid_receipt_status")
    _require_string_list(receipt.get("evidence"), "invalid_evidence", errors)
    if kind == "worker":
        if not isinstance(receipt.get("errors"), list) or not all(isinstance(item, str) for item in receipt["errors"]):
            errors.append("invalid_errors")
        if receipt.get("status") == "completed":
            if receipt.get("child_exit_code") != 0:
                errors.append("completed_without_zero_child_exit")
            if receipt.get("errors"):
                errors.append("completed_with_errors")
    return sorted(set(errors))


def apply_reviewed_outputs(
    config: DelegationConfig,
    request: DispatchRequest,
    worker_receipt: Mapping[str, Any],
    reviewer_receipt: Mapping[str, Any],
) -> None:
    """Copy approved staged outputs only after a matching independent review."""

    if not config.policy["require_independent_review_before_apply"]:
        raise BoundaryError("review gate may not be disabled")
    if validate_receipt(config, worker_receipt):
        raise BoundaryError("worker receipt is invalid")
    if worker_receipt.get("status") != "completed":
        raise BoundaryError("only a completed staged worker may be applied")
    if validate_receipt(config, reviewer_receipt):
        raise BoundaryError("reviewer receipt is invalid")
    if reviewer_receipt.get("status") != "approved":
        raise BoundaryError("reviewer did not approve application")
    if reviewer_receipt.get("reviewed_worker_digest") != receipt_digest(worker_receipt):
        raise BoundaryError("reviewer receipt does not bind this worker result")
    if reviewer_receipt.get("reviewed_output_manifest") != worker_receipt.get("output_manifest"):
        raise BoundaryError("reviewer receipt does not bind this output manifest")
    if reviewer_receipt.get("reviewed_input_manifest") != worker_receipt.get("input_manifest"):
        raise BoundaryError("reviewer receipt does not bind this staged input baseline")
    if reviewer_receipt.get("reviewed_brief_digest") != worker_receipt.get("brief_digest"):
        raise BoundaryError("reviewer receipt does not bind this controller brief")
    if reviewer_receipt.get("workflow_selection") != worker_receipt.get("workflow_selection"):
        raise BoundaryError("reviewer receipt does not bind this session workflow selection")
    if reviewer_receipt.get("reviewed_workflow_selection") != worker_receipt.get("workflow_selection"):
        raise BoundaryError("reviewer outcome does not bind this session workflow selection")
    if reviewer_receipt.get("reviewed_controller_receipt_digest") != worker_receipt.get(
        "controller_receipt_digest"
    ):
        raise BoundaryError("reviewer receipt does not bind this controller receipt")
    if reviewer_receipt.get("reviewed_controller_plan_digest") != worker_receipt.get(
        "controller_plan_digest"
    ):
        raise BoundaryError("reviewer receipt does not bind this controller plan")

    _workflow_selection_context(request.workflow_selection)
    if request.workflow_selection != worker_receipt.get("workflow_selection"):
        raise BoundaryError("apply request workflow selection does not match the worker receipt")
    if request.controller_host != worker_receipt.get("controller_host"):
        raise BoundaryError("apply request controller host does not match the worker receipt")
    if reviewer_receipt.get("controller_host") != request.controller_host:
        raise BoundaryError("reviewer controller host does not match the apply request")
    if not isinstance(request.brief, str) or not request.brief:
        raise BoundaryError("apply request brief is invalid")
    request_brief_digest = hashlib.sha256(request.brief.encode("utf-8")).hexdigest()
    if request_brief_digest != worker_receipt.get("brief_digest"):
        raise BoundaryError("apply request brief does not match the worker receipt")
    request_continuation = (
        None if request.continuation is None else request.continuation.to_manifest()
    )
    if request_continuation != worker_receipt.get("continuation"):
        raise BoundaryError("apply request continuation lineage does not match the worker receipt")
    try:
        requested_inputs = tuple(
            _relative_path(value, "input").as_posix() for value in request.inputs
        )
    except BoundaryError as exc:
        raise BoundaryError("apply request inputs are invalid") from exc
    if (
        len(requested_inputs) != len(set(requested_inputs))
        or set(requested_inputs) != set(worker_receipt.get("input_manifest", {}))
    ):
        raise BoundaryError("apply request inputs do not match the worker receipt")

    source_requested = Path(request.source_root)
    if source_requested.is_symlink() or not source_requested.is_dir():
        raise BoundaryError("source root is no longer a real directory")
    source_root = source_requested.resolve(strict=True)
    if worker_receipt.get("source_root") != str(source_root):
        raise BoundaryError("worker receipt source root does not match apply target")
    requested_outputs = tuple(
        _relative_path(value, "approved output").as_posix() for value in request.approved_outputs
    )
    if requested_outputs != tuple(worker_receipt["approved_outputs"]):
        raise BoundaryError("apply request outputs do not match the worker receipt")
    stage_root = Path(str(worker_receipt["stage_root"]))
    if stage_root.is_symlink() or not stage_root.is_dir():
        raise BoundaryError("staged worker root is unavailable")
    stage_root = stage_root.resolve(strict=True)
    if Path(request.stage_root).resolve(strict=False) != stage_root:
        raise BoundaryError("apply request stage root does not match the worker receipt")
    if reviewer_receipt.get("stage_root") != str(stage_root):
        raise BoundaryError("reviewer did not inspect this staged root")
    layout = StageLayout(
        root=stage_root,
        workspace=stage_root / "workspace",
        inputs=stage_root / "inputs",
        outputs=stage_root / "outputs",
        control=stage_root / "workspace" / "control",
        source_root=source_root,
        approved_outputs=tuple(str(item) for item in worker_receipt["approved_outputs"]),
        input_manifest={
            str(path): str(digest)
            for path, digest in dict(worker_receipt["input_manifest"]).items()
        },
        brief_digest=str(worker_receipt["brief_digest"]),
        workflow_selection=str(worker_receipt["workflow_selection"]),
    )
    _verify_persisted_worker_receipt(config, layout, worker_receipt)
    actual_inputs, input_errors = _directory_manifest(layout.inputs, label="staged_input")
    if input_errors or actual_inputs != dict(layout.input_manifest):
        raise BoundaryError("staged inputs changed after the worker receipt")
    if layout.control.is_symlink() or not layout.control.is_dir():
        raise BoundaryError("controller brief directory is unavailable")
    brief_path = _assert_no_symlink_components(
        layout.control,
        Path("brief.txt"),
        leaf_must_exist=True,
    )
    try:
        current_brief_digest = hashlib.sha256(brief_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise BoundaryError("controller brief is unavailable") from exc
    if current_brief_digest != layout.brief_digest:
        raise BoundaryError("controller brief changed after the worker receipt")
    controller_receipt = worker_receipt.get("controller_receipt")
    if not isinstance(controller_receipt, Mapping):
        raise BoundaryError("worker controller receipt is unavailable")
    controller_manifest = controller_receipt.get("request_manifest")
    if not isinstance(controller_manifest, Mapping):
        raise BoundaryError("worker controller request manifest is unavailable")
    expected_manifest = _controller_request_manifest(config, request, layout)
    if dict(controller_manifest) != expected_manifest:
        raise BoundaryError("controller request manifest does not match the apply request")
    if receipt_digest(controller_receipt) != worker_receipt.get("controller_receipt_digest"):
        raise BoundaryError("worker controller receipt changed after execution")
    _verify_controller_request(layout, controller_manifest)
    _verify_controller_plan(
        layout,
        controller_receipt,
        controller_manifest,
        worker_receipt.get("controller_plan_digest"),
    )
    actual_outputs, output_errors = _list_stage_outputs(layout)
    actual_manifest, manifest_errors = _output_manifest(layout, actual_outputs)
    if (
        output_errors
        or manifest_errors
        or sorted(actual_outputs) != sorted(worker_receipt["changed_outputs"])
        or actual_manifest != worker_receipt["output_manifest"]
    ):
        raise BoundaryError("staged outputs changed after the worker receipt")

    # Preflight every source target before creating a single directory or
    # copying a byte. This prevents a later symlink discovery from causing an
    # earlier output to mutate source state.
    targets: list[tuple[Path, Path]] = []
    for value in actual_outputs:
        relative = _relative_path(value, "approved output")
        staged_file = _assert_no_symlink_components(layout.outputs, relative, leaf_must_exist=True)
        destination = source_root / relative
        if destination.is_symlink():
            raise BoundaryError("apply destination may not be a symlink")
        _assert_no_symlink_components(source_root, relative.parent, leaf_must_exist=False)
        if destination.exists():
            if destination.is_dir():
                raise BoundaryError("apply destination may not be a directory")
            if not _is_within(destination.resolve(strict=True), source_root):
                raise BoundaryError("apply destination resolves outside the source root")
        targets.append((staged_file, destination))

    # Recheck the original source baseline and every finite ancestor at the
    # final gate, after all target preflight but immediately before this
    # function can create a directory or copy a byte into the source root.
    _verify_persisted_worker_receipt(config, layout, worker_receipt)
    _verify_worker_controller_context(config, layout, worker_receipt)
    _verify_source_baseline(layout)
    _verify_prior_attempts(config, layout, _lineage_attempts(worker_receipt))
    for staged_file, destination in targets:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged_file, destination, follow_symlinks=False)


def generate_profile_text(config: DelegationConfig, profile_name: str) -> str:
    """Render one compatibility profile directly from the single config source."""

    try:
        profile = config.compatibility_profiles[profile_name]
        binding = config.bindings[profile["binding"]]
    except KeyError as exc:
        raise ConfigError(f"unknown compatibility profile: {profile_name}") from exc
    mode = profile["mode"]
    description = {
        "legacy_readonly": "Generated compatibility profile for canonical read-only host control and review.",
        "standard_worker": "Generated compatibility profile for canonical standard execution.",
        "legacy_alias": "Generated legacy alias; canonical routing chooses Terra or DeepSeek directly.",
        "manual_only": "Generated compatibility profile for a manual-only, exact-scope takeover.",
    }.get(mode)
    if description is None:
        raise ConfigError(f"unknown compatibility profile mode: {mode}")
    instructions = (
        "This compatibility profile is generated from the canonical hi-dele-to-low "
        "delegation.toml. Resolve a fresh binding before dispatch; an already loaded "
        "profile does not prove a TOML change took effect. Do not spawn nested agents, "
        "create sidebar tasks, send external messages, or broaden the approved write "
        "boundary. Report requested values from the resolved config and actual values "
        "only from runtime telemetry; otherwise use unobserved."
    )
    return (
        f"name = {_toml_string(profile_name)}\n"
        f"description = {_toml_string(description)}\n"
        f"model = {_toml_string(binding.model)}\n"
        f"model_reasoning_effort = {_toml_string(binding.effort)}\n"
        f"sandbox_mode = {_toml_string(profile['sandbox'])}\n"
        "developer_instructions = \"\"\"\n"
        f"{instructions}\n"
        "\"\"\"\n"
    )
