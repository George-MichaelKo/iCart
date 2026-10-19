"""Offline contracts; no model runtime or external requests are needed here."""

from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock, patch

from icart.adapters.ollama import NoRedirect, OllamaModel
from icart.application.interpretation import ModelFailure, interpret
from icart.application.ports import ModelContext
from icart.application.security import OfferRegistry, PolicyViolation
from icart.application.service import ToolGateway
from icart.adapters.memory import InMemoryOfferRepository
from icart.domain.models import Principal, ValidationError


TEXT = "Get 1 litre of milk under $30 for pickup in 60601 within 6 hours."
DRAFT = {"items": [{"product_key": "milk", "quantity": 1, "unit": "l", "source_text": "1 litre of milk"}],
         "budget_cents": 3000, "postal_code": "60601", "fulfillment": "pickup", "within_hours": 6,
         "unsupported_constraints": []}
NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


class StaticIntent:
    def __init__(self, draft):
        self.draft = draft

    def extract(self, text):
        return self.draft


class InterpretationTests(unittest.TestCase):
    def interpret(self, draft):
        return interpret(TEXT, StaticIntent(draft), Principal("alice"), NOW)

    def test_valid_draft_becomes_scoped_mission(self):
        result = self.interpret(deepcopy(DRAFT))
        self.assertEqual(result.mission.owner_id, "alice")
        self.assertEqual(result.mission.budget_cents, 3000)
        self.assertEqual(result.mission.items[0].quantity, 1000)
        self.assertEqual(result.questions, ())

    def test_missing_fields_ask_without_defaulting(self):
        for key in ("budget_cents", "postal_code", "fulfillment", "within_hours"):
            draft = deepcopy(DRAFT)
            draft[key] = None
            with self.subTest(key=key):
                result = self.interpret(draft)
                self.assertIsNone(result.mission)
                self.assertEqual(len(result.questions), 1)

    def test_missing_quantity_cannot_plan(self):
        draft = deepcopy(DRAFT)
        draft["items"][0]["quantity"] = None
        self.assertIsNone(self.interpret(draft).mission)

    def test_wrong_model_unit_conversion_cannot_plan(self):
        draft = deepcopy(DRAFT)
        draft["items"][0].update(quantity=1, unit="ml")
        self.assertIsNone(self.interpret(draft).mission)

    def test_mismatched_quantity_cannot_plan(self):
        draft = deepcopy(DRAFT)
        draft["items"][0]["quantity"] = 2
        self.assertIsNone(self.interpret(draft).mission)

    def test_unsupported_constraint_cannot_plan(self):
        draft = deepcopy(DRAFT)
        draft["unsupported_constraints"] = ["severe milk allergy"]
        self.assertIsNone(self.interpret(draft).mission)

    def test_invented_evidence_is_rejected(self):
        draft = deepcopy(DRAFT)
        draft["items"][0]["source_text"] = "500 litres of milk"
        with self.assertRaises(ModelFailure):
            self.interpret(draft)

    def test_model_cannot_supply_identity_or_extra_fields(self):
        draft = dict(DRAFT, owner_id="admin")
        with self.assertRaises(ModelFailure):
            self.interpret(draft)

    def test_invalid_scalar_types_are_rejected(self):
        for key, value in (("budget_cents", True), ("budget_cents", -1), ("postal_code", 60601),
                           ("fulfillment", []), ("within_hours", True), ("within_hours", 999),
                           ("unsupported_constraints", "none")):
            draft = deepcopy(DRAFT)
            draft[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ModelFailure):
                self.interpret(draft)

    def test_invalid_item_types_are_rejected(self):
        for key, value in (("quantity", True), ("quantity", 0), ("unit", []),
                           ("product_key", "../secret"), ("source_text", 1)):
            draft = deepcopy(DRAFT)
            draft["items"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ModelFailure):
                self.interpret(draft)

    def test_empty_duplicate_or_excessive_items_rejected(self):
        for items in ([], DRAFT["items"] * 2, DRAFT["items"] * 13):
            with self.subTest(items=items), self.assertRaises(ModelFailure):
                self.interpret(dict(DRAFT, items=items))

    def test_request_limit_before_model_call(self):
        class NeverModel:
            def extract(self, text):
                raise AssertionError("must not call model")
        for text in ("", "x" * 4001):
            with self.subTest(text=len(text)), self.assertRaises(ValidationError):
                interpret(text, NeverModel(), Principal("alice"), NOW)


class AdapterTests(unittest.TestCase):
    def test_cloud_nonloopback_and_credentials_rejected(self):
        for url in ("https://api.example.com", "http://localhost:11435", "http://10.0.0.1:11435",
                    "http://127.0.0.1@evil.invalid", "http://127.0.0.1:11435/private",
                    "http://127.0.0.1:11435?redirect=cloud", "http://user:secret@127.0.0.1"):
            with self.subTest(url=url), self.assertRaises(ModelFailure):
                OllamaModel(url)

    def test_unapproved_model_is_rejected(self):
        with self.assertRaises(ModelFailure):
            OllamaModel(model="some-model:cloud")

    def test_invalid_ports_are_rejected(self):
        for url in ("http://127.0.0.1:abc", "http://127.0.0.1:0", "http://127.0.0.1:99999"):
            with self.subTest(url=url), self.assertRaises(ModelFailure):
                OllamaModel(url)

    def test_transport_caps_response_bytes(self):
        model = OllamaModel()
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"x" * 1_048_577
        with patch.object(model.opener, "open", return_value=response), self.assertRaises(ModelFailure):
            model.verify()
        response.read.assert_called_once_with(1_048_577)

    def test_transport_errors_hide_details(self):
        model = OllamaModel()
        with patch.object(model.opener, "open", side_effect=OSError("private detail")), self.assertRaises(ModelFailure) as error:
            model.verify()
        self.assertNotIn("private detail", str(error.exception))

    def test_redirects_rejected(self):
        with self.assertRaises(ModelFailure):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid")

    def test_native_tool_call_is_parsed(self):
        model = OllamaModel()
        message = {"tool_calls": [{"function": {"name": "compare_baskets", "arguments": {"offer_refs": ["offer_1", "offer_2"]}}}]}
        with patch.object(model, "_chat", return_value=message):
            call = model.propose(ModelContext(("a", "b")))
        self.assertEqual(call.name, "compare_baskets")
        self.assertEqual(call.offer_refs, ("a", "b"))

    def test_excessive_model_context_rejected_before_call(self):
        model = OllamaModel()
        with patch.object(model, "_chat") as chat, self.assertRaises(ModelFailure):
            model.propose(ModelContext(tuple(str(i) for i in range(25))))
        chat.assert_not_called()

    def test_bad_native_calls_fail_closed(self):
        model = OllamaModel()
        for calls in (None, [], ["invalid"], [{"function": "invalid"}],
                      [{"function": {"name": "send_message", "arguments": {}}}],
                      [{"function": {"name": "compare_baskets", "arguments": {"offer_refs": "bad"}}}]):
            with self.subTest(calls=calls), patch.object(model, "_chat", return_value={"tool_calls": calls}), self.assertRaises(ModelFailure):
                model.propose(ModelContext(()))

    def test_translated_refs_still_rejected_by_gateway(self):
        model = OllamaModel()
        message = {"tool_calls": [{"function": {"name": "compare_baskets", "arguments": {"offer_refs": ["offer_1"]}}}]}
        with patch.object(model, "_chat", return_value=message):
            call = model.propose(ModelContext(("forged",)))
        mission = interpret(TEXT, StaticIntent(DRAFT), Principal("alice"), NOW).mission
        gateway = ToolGateway(OfferRegistry(InMemoryOfferRepository()))
        with self.assertRaises(PolicyViolation):
            gateway.execute(Principal("alice"), mission, call, (), NOW)

    def test_invented_alias_is_rejected(self):
        model = OllamaModel()
        message = {"tool_calls": [{"function": {"name": "compare_baskets", "arguments": {"offer_refs": ["offer_999"]}}}]}
        with patch.object(model, "_chat", return_value=message), self.assertRaises(ModelFailure):
            model.propose(ModelContext(("a",)))

    def test_extraction_does_not_accept_tool_calls(self):
        model = OllamaModel()
        with patch.object(model, "_chat", return_value={"tool_calls": [{"name": "bad"}]}), self.assertRaises(ModelFailure):
            model.extract(TEXT)

    def test_truncated_generation_is_rejected(self):
        model = OllamaModel()
        with patch.object(model, "_request", return_value={"done": True, "done_reason": "length", "message": {}}), self.assertRaises(ModelFailure):
            model.extract(TEXT)

    def test_remote_model_metadata_rejected(self):
        model = OllamaModel()
        with patch.object(model, "_request", return_value={"remote_host": "cloud"}), self.assertRaises(ModelFailure):
            model.verify()
