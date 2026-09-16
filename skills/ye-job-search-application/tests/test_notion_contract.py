"""Synthetic checks for the local Notion contract helper.

These tests do not access Notion, a browser, or the live recovery queue.
"""

import copy
import importlib.util
import json
from pathlib import Path
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = SKILL_ROOT / "config" / "notion.json"
MODULE_PATH = SKILL_ROOT / "scripts" / "notion_contract.py"


def load_contract_module():
    if not MODULE_PATH.is_file():
        return None
    spec = importlib.util.spec_from_file_location("notion_contract", MODULE_PATH)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = load_contract_module()


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def observation_for(config, destination_key):
    binding = config["notion"][destination_key]
    return {
        "account": config["notion"]["expected_account"],
        "id": binding["id"],
        "url": binding["url"],
        "title": binding["expected_title"],
        "version": binding.get("expected_version"),
        "status": binding.get("expected_status"),
        "sections": list(binding.get("sections", {}).values()),
        "end_marker": binding.get("end_marker"),
        "schema": copy.deepcopy(binding["schema"]),
    }


def synthetic_event(
    destination_id,
    event_id="event-1",
    application_key="synthetic-application",
    page_id=None,
):
    return {
        "event_id": event_id,
        "application_key": application_key,
        "destination_id": destination_id,
        "page_id": page_id,
        "intended_change": {"history": "synthetic evidence"},
        "evidence_reference": "synthetic:evidence-1",
        "observed_at": "2026-09-13T00:00:00Z",
        "attempted_at": "2026-09-13T00:01:00Z",
        "failure": "synthetic write failure",
        "next_action": "inspect the original destination",
    }


def empty_queue():
    return {
        "version": 1,
        "pending": [],
        "receipts": [],
        "unrelated_history": {"preserve": True},
    }


def dashed(notion_id):
    return "-".join(
        (
            notion_id[:8],
            notion_id[8:12],
            notion_id[12:16],
            notion_id[16:20],
            notion_id[20:],
        )
    )


class NotionContractTests(unittest.TestCase):
    def contract(self):
        if CONTRACT is None:
            self.fail("notion_contract implementation is missing")
        return CONTRACT

    def validate(self, config, destination_key, observation):
        return self.contract().validate_live_observation(
            config, destination_key, observation
        )

    def readback(self, state, destination_id, page_id, **kwargs):
        return self.contract().record_readback(
            state,
            event_id=kwargs.pop("event_id", "event-1"),
            application_key=kwargs.pop("application_key", "synthetic-application"),
            destination_id=destination_id,
            page_id=page_id,
            verified_at=kwargs.pop("verified_at", "2026-09-13T00:02:00Z"),
        )

    def test_current_config_accepts_complete_live_observations(self):
        config = load_config()
        for destination_key in (
            "application_archive",
            "company_poc",
            "rules_and_templates",
            "search_runs",
        ):
            with self.subTest(destination_key=destination_key):
                result = self.validate(
                    config, destination_key, observation_for(config, destination_key)
                )
                self.assertTrue(result.valid, result.reason)

    def test_missing_or_null_binding_fails_closed(self):
        config = load_config()
        for destination_key, replacement in (
            ("rules_and_templates", None),
            ("search_runs", "missing"),
        ):
            with self.subTest(destination_key=destination_key):
                changed = copy.deepcopy(config)
                if replacement is None:
                    changed["notion"][destination_key] = None
                else:
                    del changed["notion"][destination_key]
                result = self.validate(changed, destination_key, None)
                self.assertFalse(result.valid)
                self.assertIn(destination_key, result.reason)

    def test_wrong_identity_values_fail_closed(self):
        config = load_config()
        for field, wrong_value in (
            ("account", "other-account"),
            ("id", "f" * 32),
            ("title", "other-title"),
        ):
            with self.subTest(field=field):
                observation = observation_for(config, "rules_and_templates")
                observation[field] = wrong_value
                result = self.validate(config, "rules_and_templates", observation)
                self.assertFalse(result.valid)
                self.assertIn(field, result.reason)

    def test_partial_rules_and_markers_fail_closed(self):
        config = load_config()
        cases = []

        partial = observation_for(config, "rules_and_templates")
        partial["sections"] = partial["sections"][:-1]
        cases.append(("partial", partial))

        no_end_marker = observation_for(config, "rules_and_templates")
        no_end_marker["end_marker"] = None
        cases.append(("end_marker", no_end_marker))

        wrong_version = observation_for(config, "rules_and_templates")
        wrong_version["version"] = "other-version"
        cases.append(("version", wrong_version))

        wrong_status = observation_for(config, "rules_and_templates")
        wrong_status["status"] = "other-status"
        cases.append(("status", wrong_status))

        for expected_reason, observation in cases:
            with self.subTest(expected_reason=expected_reason):
                result = self.validate(config, "rules_and_templates", observation)
                self.assertFalse(result.valid)
                self.assertIn(expected_reason, result.reason)

    def test_schema_drift_blocks_a_destination(self):
        config = load_config()
        observation = observation_for(config, "application_archive")
        observation["schema"]["properties"]["history"] = "different-field"

        result = self.validate(config, "application_archive", observation)

        self.assertFalse(result.valid)
        self.assertIn("schema", result.reason)

    def test_equally_malformed_application_config_and_observation_fail_closed(self):
        config = load_config()
        cases = []

        empty_properties = copy.deepcopy(config)
        empty_properties["notion"]["application_archive"]["schema"]["properties"] = {}
        cases.append(("properties", empty_properties))

        missing_history = copy.deepcopy(config)
        del missing_history["notion"]["application_archive"]["schema"]["properties"][
            "history"
        ]
        cases.append(("history", missing_history))

        empty_statuses = copy.deepcopy(config)
        empty_statuses["notion"]["application_archive"]["schema"][
            "status_mapping"
        ] = {}
        cases.append(("status", empty_statuses))

        for expected_reason, changed in cases:
            with self.subTest(expected_reason=expected_reason):
                result = self.validate(
                    changed,
                    "application_archive",
                    observation_for(changed, "application_archive"),
                )
                self.assertFalse(result.valid)
                self.assertIn(expected_reason, result.reason)

    def test_equally_malformed_company_template_and_page_contract_fail_closed(self):
        config = load_config()
        cases = []

        empty_company_properties = copy.deepcopy(config)
        empty_company_properties["notion"]["company_poc"]["schema"][
            "properties"
        ] = {}
        cases.append(("company_poc", "properties", empty_company_properties))

        missing_template_contact_id = copy.deepcopy(config)
        del missing_template_contact_id["notion"]["company_poc"]["schema"][
            "body_template"
        ]["contacts"]["template_data_source_id"]
        cases.append(("company_poc", "contacts", missing_template_contact_id))

        missing_page_contract = copy.deepcopy(config)
        del missing_page_contract["notion"]["search_runs"]["schema"][
            "child_page_model"
        ]
        cases.append(("search_runs", "page_contract", missing_page_contract))

        for destination_key, expected_reason, changed in cases:
            with self.subTest(destination_key=destination_key):
                result = self.validate(
                    changed,
                    destination_key,
                    observation_for(changed, destination_key),
                )
                self.assertFalse(result.valid)
                self.assertIn(expected_reason, result.reason)

    def test_notion_url_variants_with_same_page_identity_are_accepted(self):
        config = load_config()
        binding = config["notion"]["rules_and_templates"]
        observation = observation_for(config, "rules_and_templates")
        observation["url"] = (
            "https://app.notion.com/p/synthetic/Rules-"
            f"{dashed(binding['id'])}?v={'e' * 32}"
        )

        result = self.validate(config, "rules_and_templates", observation)

        self.assertTrue(result.valid, result.reason)

    def test_invalid_notion_url_origin_identity_and_side_peek_fail_closed(self):
        config = load_config()
        binding = config["notion"]["rules_and_templates"]
        cases = (
            (
                "url_origin",
                "https://www.notion.so/p/synthetic/" + binding["id"],
            ),
            ("url_malformed", "https://app.notion.com/p/synthetic/no-page-id"),
            (
                "url_malformed",
                "https://app.notion.com/p/synthetic/" + binding["id"] + "0",
            ),
            (
                "url_identity",
                "https://app.notion.com/p/synthetic/" + ("f" * 32),
            ),
            (
                "side_peek",
                "https://app.notion.com/p/synthetic/"
                + binding["id"]
                + "?p="
                + ("f" * 32),
            ),
        )

        for expected_reason, url in cases:
            with self.subTest(expected_reason=expected_reason):
                observation = observation_for(config, "rules_and_templates")
                observation["url"] = url
                result = self.validate(config, "rules_and_templates", observation)
                self.assertFalse(result.valid)
                self.assertIn(expected_reason, result.reason)

    def test_config_only_destination_and_field_change_is_supported(self):
        config = copy.deepcopy(load_config())
        binding = config["notion"]["application_archive"]
        replacement_id = "a" * 32
        binding["id"] = replacement_id
        binding["url"] = (
            "https://app.notion.com/p/synthetic/Replacement-" + replacement_id
        )
        binding["schema"]["properties"]["history"] = "replacement-history-field"

        result = self.validate(
            config,
            "application_archive",
            observation_for(config, "application_archive"),
        )

        self.assertTrue(result.valid, result.reason)

    def test_confirmed_readback_creates_one_compact_receipt(self):
        config = load_config()
        destination_id = config["notion"]["application_archive"]["id"]

        transition = self.readback(empty_queue(), destination_id, "page-1")

        self.assertEqual("synced", transition.status)
        self.assertEqual([], transition.state["pending"])
        self.assertEqual(1, len(transition.state["receipts"]))
        self.assertEqual("event-1", transition.state["receipts"][0]["event_id"])
        self.assertEqual({"preserve": True}, transition.state["unrelated_history"])

    def test_failed_write_is_queued_without_touching_unrelated_history(self):
        config = load_config()
        event = synthetic_event(config["notion"]["application_archive"]["id"])

        transition = self.contract().queue_failed_write(empty_queue(), event)

        self.assertEqual("queued", transition.status)
        self.assertEqual([event], transition.state["pending"])
        self.assertEqual({"preserve": True}, transition.state["unrelated_history"])

    def test_existing_event_recovery_removes_pending_without_replaying(self):
        config = load_config()
        destination_id = config["notion"]["application_archive"]["id"]
        queued = self.contract().queue_failed_write(
            empty_queue(), synthetic_event(destination_id)
        ).state

        transition = self.contract().recover_existing_event(
            queued,
            event_id="event-1",
            application_key="synthetic-application",
            destination_id=destination_id,
            page_id="already-written-page",
            verified_at="2026-09-13T00:03:00Z",
        )

        self.assertEqual("recovered_existing_event", transition.status)
        self.assertEqual([], transition.state["pending"])
        self.assertEqual(1, len(transition.state["receipts"]))

    def test_duplicate_replay_does_not_append_a_second_event_or_receipt(self):
        config = load_config()
        destination_id = config["notion"]["application_archive"]["id"]
        synced = self.readback(empty_queue(), destination_id, "page-1").state

        queued_again = self.contract().queue_failed_write(
            synced, synthetic_event(destination_id)
        )
        reread = self.readback(synced, destination_id, "page-1")

        self.assertEqual("already_synced", queued_again.status)
        self.assertEqual(synced, queued_again.state)
        self.assertEqual("already_synced", reread.status)
        self.assertEqual(synced, reread.state)

    def test_changed_destination_keeps_pending_payload_for_manual_reconciliation(self):
        config = load_config()
        old_destination_id = config["notion"]["application_archive"]["id"]
        queued = self.contract().queue_failed_write(
            empty_queue(), synthetic_event(old_destination_id)
        ).state

        disposition = self.contract().replay_disposition(
            queued,
            "event-1",
            "f" * 32,
            application_key="synthetic-application",
        )
        transition = self.readback(queued, "f" * 32, "replacement-page")

        self.assertEqual("destination_changed_retained_pending", disposition)
        self.assertEqual("destination_mismatch_pending", transition.status)
        self.assertEqual(queued, transition.state)

    def test_pending_update_rejects_conflicting_application_identity(self):
        config = load_config()
        destination_id = config["notion"]["application_archive"]["id"]
        queued = self.contract().queue_failed_write(
            empty_queue(), synthetic_event(destination_id, application_key="application-a")
        ).state

        transition = self.contract().queue_failed_write(
            queued,
            synthetic_event(destination_id, application_key="application-b"),
        )

        self.assertEqual("application_conflict_retained_pending", transition.status)
        self.assertEqual(queued, transition.state)

    def test_pending_update_rejects_conflicting_known_page_identity(self):
        config = load_config()
        destination_id = config["notion"]["application_archive"]["id"]
        queued = self.contract().queue_failed_write(
            empty_queue(), synthetic_event(destination_id, page_id="known-page-a")
        ).state

        transition = self.contract().queue_failed_write(
            queued, synthetic_event(destination_id, page_id="known-page-b")
        )

        self.assertEqual("page_conflict_retained_pending", transition.status)
        self.assertEqual(queued, transition.state)

    def test_pending_update_resolves_an_unknown_page_only_with_matching_identity(self):
        config = load_config()
        destination_id = config["notion"]["application_archive"]["id"]
        queued = self.contract().queue_failed_write(
            empty_queue(), synthetic_event(destination_id)
        ).state

        transition = self.contract().queue_failed_write(
            queued, synthetic_event(destination_id, page_id="known-page")
        )

        self.assertEqual("updated_pending_page_resolved", transition.status)
        self.assertEqual("known-page", transition.state["pending"][0]["page_id"])
        self.assertEqual("synthetic-application", transition.state["pending"][0]["application_key"])
        self.assertEqual(destination_id, transition.state["pending"][0]["destination_id"])

    def test_readback_rejects_a_conflicting_application_or_known_page(self):
        config = load_config()
        destination_id = config["notion"]["application_archive"]["id"]
        application_queued = self.contract().queue_failed_write(
            empty_queue(), synthetic_event(destination_id, application_key="application-a")
        ).state
        page_queued = self.contract().queue_failed_write(
            empty_queue(), synthetic_event(destination_id, page_id="known-page-a")
        ).state

        application_transition = self.readback(
            application_queued,
            destination_id,
            "known-page",
            application_key="application-b",
        )
        page_transition = self.readback(
            page_queued,
            destination_id,
            "known-page-b",
        )

        self.assertEqual("application_mismatch_pending", application_transition.status)
        self.assertEqual(application_queued, application_transition.state)
        self.assertEqual("page_mismatch_pending", page_transition.status)
        self.assertEqual(page_queued, page_transition.state)

    def test_readback_resolves_an_unknown_pending_page_with_matching_identity(self):
        config = load_config()
        destination_id = config["notion"]["application_archive"]["id"]
        queued = self.contract().queue_failed_write(
            empty_queue(), synthetic_event(destination_id)
        ).state

        transition = self.readback(queued, destination_id, "known-page")

        self.assertEqual("synced", transition.status)
        self.assertEqual([], transition.state["pending"])
        self.assertEqual("known-page", transition.state["receipts"][0]["page_id"])

    def test_receipt_identity_conflicts_are_not_reported_as_synced(self):
        config = load_config()
        destination_id = config["notion"]["application_archive"]["id"]
        synced = self.readback(empty_queue(), destination_id, "known-page").state

        page_queue = self.contract().queue_failed_write(
            synced, synthetic_event(destination_id, page_id="other-page")
        )
        destination_queue = self.contract().queue_failed_write(
            synced, synthetic_event("f" * 32)
        )
        page_readback = self.readback(synced, destination_id, "other-page")
        destination_readback = self.readback(synced, "f" * 32, "known-page")

        self.assertEqual("receipt_page_conflict", page_queue.status)
        self.assertEqual("receipt_destination_conflict", destination_queue.status)
        self.assertEqual("receipt_page_conflict", page_readback.status)
        self.assertEqual("receipt_destination_conflict", destination_readback.status)
        self.assertEqual(synced, page_queue.state)
        self.assertEqual(synced, destination_queue.state)
        self.assertEqual(synced, page_readback.state)
        self.assertEqual(synced, destination_readback.state)




class FinalRegressionTests(NotionContractTests):
    # Inherited suite is excluded below; these tests only add targeted regressions.
    def test_invalid_internal_storage_references(self):
        for key, change in (
            ("application_archive", lambda schema: schema["history_storage"].update(location="unconfigured_store")),
            ("application_archive", lambda schema: schema.update(empty_summary_properties=["missing-column"])),
            ("company_poc", lambda schema: schema["body_template"]["contacts"].update(group_by="missing-column")),
        ):
            config=load_config(); change(config["notion"][key]["schema"])
            result=self.validate(config,key,observation_for(config,key))
            self.assertFalse(result.valid,result.reason)

    def test_malformed_percent_escapes_are_rejected(self):
        config=load_config(); key="rules_and_templates"
        for suffix in ("%ZZ", "%", "%2", "%FF", "?q=%GG", "#%q1"):
            observation=observation_for(config,key); observation["url"]+=suffix
            self.assertFalse(self.validate(config,key,observation).valid,suffix)
        observation=observation_for(config,key); observation["url"]+="?q=%25ZZ"
        self.assertTrue(self.validate(config,key,observation).valid)

    def test_receipt_pending_conflicts_precede_synced_for_all_paths(self):
        destination="destination-1"; event=synthetic_event(destination,page_id="page-b")
        base=empty_queue(); base["pending"]=[event]
        base["receipts"]=[{"event_id":"event-1","destination_id":destination,"page_id":"page-a","verified_at":"verified"}]
        unknown=synthetic_event(destination)
        before=copy.deepcopy(base)
        failed=self.contract().queue_failed_write(base,unknown)
        self.assertIn("conflict",failed.status); self.assertEqual(before,failed.state)
        replay=self.contract().replay_disposition(base,"event-1",destination)
        self.assertIn("conflict",replay)
        readback=self.contract().record_readback(base,event_id="event-1",application_key=event["application_key"],destination_id=destination,page_id="page-a",verified_at="now")
        self.assertIn("conflict",readback.status); self.assertEqual(before,readback.state)
        self.assertEqual(before,base)

    def test_receipt_pending_application_and_destination_conflicts(self):
        for changed_field, value in (("application_key","other-app"),("destination_id","other-destination")):
            queue=empty_queue(); pending=synthetic_event("destination-1",page_id="page-a")
            pending[changed_field]=value; queue["pending"]=[pending]
            queue["receipts"]=[{"event_id":"event-1","destination_id":"destination-1","page_id":"page-a","verified_at":"old"}]
            result=self.contract().queue_failed_write(queue,synthetic_event("destination-1"))
            self.assertIn("conflict",result.status); self.assertEqual(queue,result.state)
            self.assertIn("conflict",self.contract().replay_disposition(queue,"event-1","destination-1",application_key="synthetic-application"))

    def test_consistent_existing_receipt_readback_clears_pending_once(self):
        queue=empty_queue(); queue["pending"]=[synthetic_event("destination-1",page_id="page-a")]
        queue["receipts"]=[{"event_id":"event-1","destination_id":"destination-1","page_id":"page-a","verified_at":"old"}]
        result=self.contract().record_readback(queue,event_id="event-1",application_key="synthetic-application",destination_id="destination-1",page_id="page-a",verified_at="now")
        self.assertEqual("already_synced",result.status); self.assertEqual([],result.state["pending"])
        self.assertEqual(queue["receipts"],result.state["receipts"])

    def test_conflicting_receipts_without_known_incoming_page(self):
        queue=empty_queue(); queue["receipts"]=[{"event_id":"event-1","destination_id":"destination-1","page_id":page,"verified_at":"old"} for page in ("page-a","page-b")]
        result=self.contract().queue_failed_write(queue,synthetic_event("destination-1"))
        self.assertIn("conflict",result.status); self.assertEqual(queue,result.state)

# Avoid running inherited methods twice while reusing setup helpers.
for _name in list(NotionContractTests.__dict__):
    if _name.startswith("test_"):
        setattr(FinalRegressionTests, _name, None)

if __name__ == "__main__":
    unittest.main()
