"""Pure validation and recovery-state helpers for the career Notion contract.

This module never opens a browser, reads Notion, writes Notion, reads the live
queue, or persists a queue. A host supplies already-read observations and is
responsible for all live actions and atomic persistence. Observation ``schema``
values are host-normalized contract projections; they are not a claim that live
Notion page content exposes policy strings as raw schema.
"""

from collections.abc import Mapping
import copy
import re
from urllib.parse import parse_qsl, unquote, urlparse


SUPPORTED_CONFIG_VERSION = 2
DATABASE_DESTINATIONS = frozenset({"application_archive", "company_poc"})
PAGE_DESTINATIONS = frozenset({"rules_and_templates", "search_runs"})
ALL_DESTINATIONS = DATABASE_DESTINATIONS | PAGE_DESTINATIONS
NOTION_ORIGIN = "https://app.notion.com"
NOTION_ID_TOKEN = (
    r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
NOTION_ID_PATTERN = re.compile(NOTION_ID_TOKEN)
NOTION_ID_SEARCH_PATTERN = re.compile(
    rf"(?<![0-9a-fA-F]){NOTION_ID_TOKEN}(?![0-9a-fA-F])"
)
REQUIRED_EVENT_FIELDS = frozenset(
    {
        "event_id",
        "application_key",
        "destination_id",
        "page_id",
        "intended_change",
        "evidence_reference",
        "observed_at",
        "attempted_at",
        "failure",
        "next_action",
    }
)
APPLICATION_PROPERTY_KEYS = frozenset(
    {
        "title",
        "company",
        "status",
        "applied_date",
        "interview_date",
        "contact",
        "history",
        "url",
    }
)
APPLICATION_STATUS_KEYS = frozenset(
    {
        "submitted_browser_confirmed",
        "submitted_user_confirmed",
        "prefilled_submission_unconfirmed",
        "prefilled_needs_review",
        "ready_for_user_review",
        "researching",
        "skipped",
    }
)
COMPANY_PROPERTY_KEYS = frozenset({"title", "funding_stage", "checked_at"})
RULES_SECTION_KEYS = frozenset(
    {
        "job_search",
        "application_archive",
        "company_networking",
        "company_poc",
        "outreach_templates",
        "experience_guidance",
        "cta_selection",
        "sources_and_boundaries",
    }
)
SEARCH_RUNS_SECTION_KEYS = frozenset(
    {"blank_batch_template", "candidate_results", "verification_receipts"}
)


class ValidationResult:
    """The result of checking one host-supplied live observation."""

    def __init__(self, valid, reason=None):
        self.valid = valid
        self.reason = reason


class QueueTransition:
    """A proposed in-memory queue state and its disposition."""

    def __init__(self, state, status):
        self.state = state
        self.status = status


def _invalid(reason):
    return ValidationResult(False, reason)


def _nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def _nonempty_text_list(value):
    return isinstance(value, (list, tuple)) and bool(value) and all(
        _nonempty_text(item) for item in value
    )


def _normalized_notion_id(value):
    """Return canonical lowercase Notion identity, or ``None`` when invalid."""

    if not _nonempty_text(value):
        return None
    raw = value.strip()
    if not NOTION_ID_PATTERN.fullmatch(raw):
        return None
    return raw.replace("-", "").lower()


def _embedded_notion_ids(value):
    """Return canonical page identities embedded in a path or side-peek value."""

    if not isinstance(value, str):
        return set()
    return {
        match.group(0).replace("-", "").lower()
        for match in NOTION_ID_SEARCH_PATTERN.finditer(unquote(value))
    }


def _notion_url_error(url, expected_id):
    """Validate the approved Notion origin and embedded page identity offline."""

    if not _nonempty_text(url):
        return "url_malformed"
    if re.search(r"%(?![0-9a-fA-F]{2})", url) or any(ch.isspace() or ord(ch) < 32 for ch in url):
        return "url_malformed"
    try:
        unquote(url, errors="strict")
        parsed = urlparse(url)
    except (ValueError, UnicodeError):
        return "url_malformed"

    if (
        parsed.scheme.lower() != "https"
        or parsed.netloc.lower() != "app.notion.com"
        or parsed.username is not None
        or parsed.password is not None
    ):
        return "url_origin_mismatch"

    path_ids = _embedded_notion_ids(parsed.path)
    if not path_ids:
        return "url_malformed"
    if len(path_ids) != 1:
        return "url_identity_conflict"
    if expected_id not in path_ids:
        return "url_identity_mismatch"

    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key != "p":
            continue
        side_peek_ids = _embedded_notion_ids(value)
        if len(side_peek_ids) != 1:
            return "url_side_peek_malformed"
        if expected_id not in side_peek_ids:
            return "url_side_peek_mismatch"

    return None


def _mapping_error(mapping, required_keys, prefix):
    if not isinstance(mapping, Mapping) or not mapping:
        return f"{prefix}_missing"
    for key in required_keys:
        if not _nonempty_text(mapping.get(key)):
            return f"{prefix}_{key}_missing"
    return None


def _template_error(template, prefix):
    if not isinstance(template, Mapping):
        return f"{prefix}_missing"

    template_id = _normalized_notion_id(template.get("id"))
    if template_id is None:
        return f"{prefix}_id_invalid"
    if not _nonempty_text(template.get("expected_title")):
        return f"{prefix}_title_missing"
    url_error = _notion_url_error(template.get("url"), template_id)
    if url_error:
        return f"{prefix}_{url_error}"

    contacts = template.get("contacts")
    if not isinstance(contacts, Mapping):
        return f"{prefix}_contacts_missing"
    if not _nonempty_text(contacts.get("expected_title")):
        return f"{prefix}_contacts_title_missing"
    if _normalized_notion_id(contacts.get("template_data_source_id")) is None:
        return f"{prefix}_contacts_template_data_source_id_missing"
    if not _nonempty_text_list(contacts.get("columns")):
        return f"{prefix}_contacts_columns_missing"
    if not _nonempty_text(template.get("instance_resolution")):
        return f"{prefix}_instance_resolution_missing"
    return None


def _database_schema_error(destination_key, binding):
    schema = binding["schema"]
    if schema.get("object_kind") != "database":
        return f"{destination_key}:schema_object_kind_invalid"
    if _normalized_notion_id(binding.get("all_view_id")) is None:
        return f"{destination_key}:all_view_id_invalid"

    if destination_key == "application_archive":
        error = _mapping_error(
            schema.get("properties"),
            APPLICATION_PROPERTY_KEYS,
            f"{destination_key}:properties",
        )
        if error:
            return error
        error = _mapping_error(
            schema.get("status_mapping"),
            APPLICATION_STATUS_KEYS,
            f"{destination_key}:status_mapping",
        )
        if error:
            return error
        dedup_key = schema.get("dedup_key")
        if not _nonempty_text_list(dedup_key) or not {
            "company",
            "requisition_id_or_canonical_url",
        }.issubset(dedup_key):
            return f"{destination_key}:dedup_key_invalid"
        if not _nonempty_text_list(schema.get("empty_summary_properties")):
            return f"{destination_key}:empty_summary_properties_missing"
        if not set(schema["empty_summary_properties"]).issubset(schema["properties"].values()):
            return f"{destination_key}:empty_summary_property_reference_invalid"

        history_storage = schema.get("history_storage")
        if not isinstance(history_storage, Mapping):
            return f"{destination_key}:history_storage_missing"
        property_key = history_storage.get("property_key")
        if property_key != "history" or not _nonempty_text(
            schema["properties"].get(property_key)
        ):
            return f"{destination_key}:history_binding_invalid"
        if history_storage.get("location") != "application_property":
            return f"{destination_key}:history_location_unsupported"
        if not isinstance(history_storage.get("body_notes_section"), bool):
            return f"{destination_key}:history_body_notes_section_invalid"

    if destination_key == "company_poc":
        error = _mapping_error(
            schema.get("properties"),
            COMPANY_PROPERTY_KEYS,
            f"{destination_key}:properties",
        )
        if error:
            return error
        company_name_link = schema.get("company_name_link")
        if not isinstance(company_name_link, Mapping):
            return f"{destination_key}:company_name_link_missing"
        if company_name_link.get("property_key") != "title":
            return f"{destination_key}:company_name_link_property_invalid"
        if not _nonempty_text(company_name_link.get("target")):
            return f"{destination_key}:company_name_link_target_missing"
        if not _nonempty_text(company_name_link.get("storage")):
            return f"{destination_key}:company_name_link_storage_missing"
        if not isinstance(company_name_link.get("separate_url_property"), bool):
            return f"{destination_key}:company_name_link_storage_invalid"
        if not _nonempty_text_list(schema.get("omitted_company_properties")):
            return f"{destination_key}:omitted_company_properties_missing"
        if not _nonempty_text(schema.get("schema_policy")):
            return f"{destination_key}:schema_policy_missing"

    template_error = _template_error(
        schema.get("body_template"), f"{destination_key}:body_template"
    )
    if template_error:
        return template_error

    if destination_key == "company_poc":
        contacts = schema["body_template"]["contacts"]
        if not _nonempty_text(contacts.get("group_by")):
            return f"{destination_key}:contacts_group_by_missing"
        if contacts["group_by"] not in contacts["columns"]:
            return f"{destination_key}:contacts_group_by_reference_invalid"
        if not _nonempty_text_list(contacts.get("relationship_types")):
            return f"{destination_key}:contacts_relationship_types_missing"
    return None


def _page_schema_error(destination_key, binding):
    schema = binding["schema"]
    if schema.get("object_kind") != "page":
        return f"{destination_key}:schema_object_kind_invalid"
    if not _nonempty_text(binding.get("expected_version")):
        return f"{destination_key}:version_missing"
    if not _nonempty_text(binding.get("end_marker")):
        return f"{destination_key}:end_marker_missing"

    required_sections = (
        RULES_SECTION_KEYS
        if destination_key == "rules_and_templates"
        else SEARCH_RUNS_SECTION_KEYS
    )
    error = _mapping_error(
        binding.get("sections"), required_sections, f"{destination_key}:sections"
    )
    if error:
        return error

    if destination_key == "rules_and_templates":
        if not _nonempty_text(binding.get("expected_status")):
            return f"{destination_key}:status_missing"
        if not _nonempty_text(schema.get("content_model")):
            return f"{destination_key}:page_contract_missing"
        if schema.get("requires_full_read") is not True:
            return f"{destination_key}:page_contract_full_read_missing"
    else:
        if not _nonempty_text(schema.get("child_page_model")):
            return f"{destination_key}:page_contract_missing"
        for field in (
            "batch_template_fields",
            "candidate_fields",
            "receipt_fields",
        ):
            if not _nonempty_text_list(schema.get(field)):
                return f"{destination_key}:page_contract_{field}_missing"
    return None


def _config_error(config):
    if not isinstance(config, Mapping):
        return "config_not_mapping"
    if config.get("version") != SUPPORTED_CONFIG_VERSION:
        return "unsupported_config_version"
    if not _nonempty_text(config.get("operational_state_path")):
        return "operational_state_path_missing"

    notion = config.get("notion")
    if not isinstance(notion, Mapping):
        return "notion_binding_missing"
    if not _nonempty_text(notion.get("expected_account")):
        return "expected_account_missing"
    return None


def _binding(config, destination_key):
    error = _config_error(config)
    if error:
        return None, error

    notion = config["notion"]
    if destination_key not in ALL_DESTINATIONS:
        return None, f"{destination_key}:destination_unknown"
    if destination_key not in notion:
        return None, f"{destination_key}:binding_missing"
    binding = notion[destination_key]
    if binding is None:
        return None, f"{destination_key}:binding_null"
    if not isinstance(binding, Mapping):
        return None, f"{destination_key}:binding_invalid"

    binding_id = _normalized_notion_id(binding.get("id"))
    if binding_id is None:
        return None, f"{destination_key}:id_invalid"
    if not _nonempty_text(binding.get("expected_title")):
        return None, f"{destination_key}:expected_title_missing"
    if not _nonempty_text(binding.get("preferred_access")):
        return None, f"{destination_key}:preferred_access_missing"
    url_error = _notion_url_error(binding.get("url"), binding_id)
    if url_error:
        return None, f"{destination_key}:binding_{url_error}"
    if not isinstance(binding.get("schema"), Mapping):
        return None, f"{destination_key}:schema_missing"

    if destination_key in DATABASE_DESTINATIONS:
        error = _database_schema_error(destination_key, binding)
    else:
        error = _page_schema_error(destination_key, binding)
    if error:
        return None, error
    return binding, None


def validate_config(config, destination_keys=None):
    """Validate complete logical bindings before any observation comparison.

    Callers may pass the destination keys needed by the current action. With no
    keys, every configured career destination must be structurally complete.
    """

    keys = ALL_DESTINATIONS if destination_keys is None else destination_keys
    if not isinstance(keys, (list, tuple, set, frozenset)):
        return _invalid("destination_keys_invalid")
    for destination_key in keys:
        _, error = _binding(config, destination_key)
        if error:
            return _invalid(error)
    return ValidationResult(True)


def validate_live_observation(config, destination_key, observation):
    """Fail closed unless a host observation matches a complete fresh binding.

    The host supplies this projection only after the live read it performed. The
    helper validates account, identity, approved-origin URL/page identity,
    title, normalized schema projection, and configured page checks without
    fetching, expanding, or inferring any live content.
    """

    binding, error = _binding(config, destination_key)
    if error:
        return _invalid(error)
    if not isinstance(observation, Mapping):
        return _invalid(f"{destination_key}:observation_missing")

    if observation.get("account") != config["notion"]["expected_account"]:
        return _invalid(f"{destination_key}:account_mismatch")

    expected_id = _normalized_notion_id(binding["id"])
    observed_id = _normalized_notion_id(observation.get("id"))
    if observed_id != expected_id:
        return _invalid(f"{destination_key}:id_mismatch")
    url_error = _notion_url_error(observation.get("url"), expected_id)
    if url_error:
        return _invalid(f"{destination_key}:{url_error}")
    if observation.get("title") != binding["expected_title"]:
        return _invalid(f"{destination_key}:title_mismatch")
    if observation.get("schema") != binding["schema"]:
        return _invalid(f"{destination_key}:schema_mismatch")

    if destination_key in PAGE_DESTINATIONS:
        if observation.get("version") != binding["expected_version"]:
            return _invalid(f"{destination_key}:version_mismatch")
        if destination_key == "rules_and_templates" and observation.get(
            "status"
        ) != binding["expected_status"]:
            return _invalid(f"{destination_key}:status_mismatch")
        observed_sections = observation.get("sections")
        if not isinstance(observed_sections, (list, tuple, set)):
            return _invalid(f"{destination_key}:sections_missing")
        missing = set(binding["sections"].values()).difference(observed_sections)
        if missing:
            return _invalid(f"{destination_key}:partial_sections")
        if observation.get("end_marker") != binding["end_marker"]:
            return _invalid(f"{destination_key}:end_marker_mismatch")

    return ValidationResult(True)


def _copy_queue(state):
    if not isinstance(state, Mapping):
        raise ValueError("queue must be a mapping")
    if state.get("version") != 1:
        raise ValueError("queue version must be 1")
    if not isinstance(state.get("pending"), list):
        raise ValueError("queue pending must be a list")
    if not isinstance(state.get("receipts"), list):
        raise ValueError("queue receipts must be a list")
    return copy.deepcopy(dict(state))


def _valid_page_id(value):
    return value is None or _nonempty_text(value)


def _validate_event(event):
    if not isinstance(event, Mapping):
        raise ValueError("pending event must be a mapping")
    missing = REQUIRED_EVENT_FIELDS.difference(event)
    if missing:
        raise ValueError(f"pending event missing fields: {sorted(missing)}")
    for field in ("event_id", "application_key", "destination_id"):
        if not _nonempty_text(event[field]):
            raise ValueError(f"pending event has invalid {field}")
    if not _valid_page_id(event["page_id"]):
        raise ValueError("pending event has invalid page_id")


def _receipts_for(queue, event_id):
    return [
        receipt
        for receipt in queue["receipts"]
        if isinstance(receipt, Mapping) and receipt.get("event_id") == event_id
    ]


def _receipt_status(queue, event_id, destination_id, page_id, *, application_key=None):
    """Return a receipt disposition after checking destination/page identity."""

    receipts = _receipts_for(queue, event_id)
    if not receipts:
        return None
    for receipt in receipts:
        receipt_destination = receipt.get("destination_id")
        receipt_page = receipt.get("page_id")
        if not _nonempty_text(receipt_destination) or not _nonempty_text(receipt_page):
            return "receipt_identity_invalid"
        if receipt_destination != destination_id:
            return "receipt_destination_conflict"
        if page_id is not None and receipt_page != page_id:
            return "receipt_page_conflict"
    receipt_pages = {receipt["page_id"] for receipt in receipts}
    if len(receipt_pages) != 1:
        return "receipt_page_conflict"
    receipt_page = next(iter(receipt_pages))
    pending_keys = set()
    for pending in queue["pending"]:
        if not isinstance(pending, Mapping) or pending.get("event_id") != event_id:
            continue
        try:
            _validate_event(pending)
        except ValueError:
            return "invalid_existing_pending_retained"
        pending_keys.add(pending["application_key"])
        if pending["destination_id"] != destination_id:
            return "receipt_pending_destination_conflict"
        if application_key is not None and pending["application_key"] != application_key:
            return "receipt_pending_application_conflict"
        if pending["page_id"] is not None and pending["page_id"] != receipt_page:
            return "receipt_pending_page_conflict"
    if len(pending_keys) > 1:
        return "receipt_pending_application_conflict"
    return "already_synced"


def _pending_index(queue, event_id):
    for index, pending in enumerate(queue["pending"]):
        if isinstance(pending, Mapping) and pending.get("event_id") == event_id:
            return index
    return None


def queue_failed_write(state, event):
    """Propose an idempotent update after a host-observed write failure.

    Established application, destination, and known page identities are never
    overwritten. Conflicts return the original pending payload unchanged.
    """

    _validate_event(event)
    queue = _copy_queue(state)
    event_id = event["event_id"]

    receipt_status = _receipt_status(
        queue, event_id, event["destination_id"], event["page_id"],
        application_key=event["application_key"]
    )
    if receipt_status:
        return QueueTransition(queue, receipt_status)

    index = _pending_index(queue, event_id)
    if index is None:
        queue["pending"].append(copy.deepcopy(dict(event)))
        return QueueTransition(queue, "queued")

    existing = queue["pending"][index]
    try:
        _validate_event(existing)
    except ValueError:
        return QueueTransition(queue, "invalid_existing_pending_retained")
    if existing["destination_id"] != event["destination_id"]:
        return QueueTransition(queue, "destination_conflict_retained_pending")
    if existing["application_key"] != event["application_key"]:
        return QueueTransition(queue, "application_conflict_retained_pending")
    existing_page = existing["page_id"]
    incoming_page = event["page_id"]
    if existing_page is not None and incoming_page is not None and existing_page != incoming_page:
        return QueueTransition(queue, "page_conflict_retained_pending")

    updated = copy.deepcopy(dict(existing))
    for key, value in event.items():
        if key not in {"event_id", "application_key", "destination_id", "page_id"}:
            updated[key] = copy.deepcopy(value)
    page_resolved = existing_page is None and incoming_page is not None
    if page_resolved:
        updated["page_id"] = incoming_page
    queue["pending"][index] = updated
    status = "updated_pending_page_resolved" if page_resolved else "updated_pending"
    return QueueTransition(queue, status)


def record_readback(
    state, *, event_id, application_key, destination_id, page_id, verified_at
):
    """Propose a compact receipt only after host-verified live readback."""

    for field, value in (
        ("event_id", event_id),
        ("application_key", application_key),
        ("destination_id", destination_id),
        ("page_id", page_id),
        ("verified_at", verified_at),
    ):
        if not _nonempty_text(value):
            raise ValueError(f"readback has invalid {field}")

    queue = _copy_queue(state)
    receipt_status = _receipt_status(
        queue, event_id, destination_id, page_id, application_key=application_key
    )
    if receipt_status:
        if receipt_status == "already_synced":
            queue["pending"] = [
                item for item in queue["pending"]
                if not (isinstance(item, Mapping) and item.get("event_id") == event_id)
            ]
        return QueueTransition(queue, receipt_status)

    index = _pending_index(queue, event_id)
    if index is not None:
        pending = queue["pending"][index]
        try:
            _validate_event(pending)
        except ValueError:
            return QueueTransition(queue, "invalid_existing_pending_retained")
        if pending["destination_id"] != destination_id:
            return QueueTransition(queue, "destination_mismatch_pending")
        if pending["application_key"] != application_key:
            return QueueTransition(queue, "application_mismatch_pending")
        if pending["page_id"] is not None and pending["page_id"] != page_id:
            return QueueTransition(queue, "page_mismatch_pending")
        del queue["pending"][index]

    queue["receipts"].append(
        {
            "event_id": event_id,
            "destination_id": destination_id,
            "page_id": page_id,
            "verified_at": verified_at,
        }
    )
    return QueueTransition(queue, "synced")


def recover_existing_event(
    state, *, event_id, application_key, destination_id, page_id, verified_at
):
    """Resolve a pending event already found in its original destination."""

    transition = record_readback(
        state,
        event_id=event_id,
        application_key=application_key,
        destination_id=destination_id,
        page_id=page_id,
        verified_at=verified_at,
    )
    if transition.status == "synced":
        return QueueTransition(transition.state, "recovered_existing_event")
    return transition


def replay_disposition(
    state, event_id, current_destination_id, *, application_key=None, page_id=None
):
    """Describe whether a pending event may be inspected at its original target.

    A host must inspect the original event before a retry. This helper never
    performs a retry, remaps a payload, or accepts a receipt without matching
    destination and (when known) page identity.
    """

    queue = _copy_queue(state)
    receipt_status = _receipt_status(
        queue, event_id, current_destination_id, page_id, application_key=application_key
    )
    if receipt_status:
        return receipt_status
    index = _pending_index(queue, event_id)
    if index is None:
        return "not_pending"
    pending = queue["pending"][index]
    try:
        _validate_event(pending)
    except ValueError:
        return "invalid_existing_pending_retained"
    if pending["destination_id"] != current_destination_id:
        return "destination_changed_retained_pending"
    if application_key is not None and pending["application_key"] != application_key:
        return "application_changed_retained_pending"
    if page_id is not None and pending["page_id"] is not None and pending["page_id"] != page_id:
        return "page_changed_retained_pending"
    return "eligible_after_live_inspection"
