"""Offline teaching example: scripted extraction, no conversational parser or storage."""

from datetime import datetime, timezone

from icart.application.drafts import confirm_draft, revise_draft, start_draft
from icart.domain.models import Principal


class ExampleIntent:
    def __init__(self, budget=None):
        self.budget = budget

    def extract(self, text):
        return {"items": [{"product_key": "eggs", "quantity": 12, "unit": "count", "source_text": "12 eggs"}],
                "budget_cents": self.budget, "postal_code": "60601", "fulfillment": "pickup",
                "within_hours": 6, "unsupported_constraints": []}


def main():
    now = datetime.now(timezone.utc)
    principal = Principal("local-demo-user")
    text = "Get 12 eggs for pickup in 60601 within 6 hours."
    draft = start_draft(text, ExampleIntent(), principal, now)
    print("SCRIPTED LIFECYCLE DEMO — no model calls, storage, jobs, or purchases")
    print(f"Revision {draft.revision}: {draft.state}")
    print(*draft.questions, sep="\n")
    # This example supplies a full corrected request. Parsing '$20' as a follow-up
    # and associating it with a saved conversation is the next integration step.
    draft = revise_draft(draft, text + " Budget $20.", ExampleIntent(2000), principal, draft.revision)
    print(f"Revision {draft.revision}: {draft.state} — budget $20.00; awaiting approval")
    draft = confirm_draft(draft, principal, draft.revision, now)
    print(f"Revision {draft.revision}: {draft.state} — approved for planning, not submitted")


if __name__ == "__main__":
    main()
