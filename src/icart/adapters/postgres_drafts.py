"""Owner-scoped draft storage. Transactions contain no model or provider calls."""

from psycopg.types.json import Jsonb

from icart.adapters.draft_serialization import draft_from_data, draft_to_data
from icart.adapters.postgres import PostgresStore
from icart.application.drafts import RevisionConflict, ShoppingDraft, confirm_draft
from icart.application.security import PolicyViolation
from icart.domain.models import Principal, ValidationError


class PostgresDraftRepository:
    def __init__(self, database: PostgresStore):
        self.database = database

    @staticmethod
    def _payload(principal: Principal, draft: ShoppingDraft) -> dict:
        if principal.user_id != draft.owner_id:
            raise PolicyViolation("draft access denied")
        data = draft_to_data(draft)
        draft_from_data(data)
        return data

    @staticmethod
    def _read(row) -> ShoppingDraft:
        if row is None:
            raise PolicyViolation("draft access denied")
        draft = draft_from_data(row["payload"])
        if (draft.id, draft.owner_id, draft.revision, draft.state.value) != (
                row["id"], row["owner_id"], row["revision"], row["state"]):
            raise ValidationError("draft record metadata does not match its snapshot")
        return draft

    def create(self, principal: Principal, draft: ShoppingDraft) -> None:
        data = self._payload(principal, draft)
        if draft.revision != 1 or draft.confirmed:
            raise ValidationError("new drafts must start unconfirmed at revision 1")
        with self.database.connect() as conn:
            row = conn.execute("""INSERT INTO icart_drafts(id,owner_id,revision,state,payload)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
                (draft.id, principal.user_id, draft.revision, draft.state.value, Jsonb(data))).fetchone()
            if row is None:
                raise RevisionConflict("draft already exists")

    def load(self, principal: Principal, draft_id: str) -> ShoppingDraft:
        with self.database.connect() as conn:
            row = conn.execute("SELECT * FROM icart_drafts WHERE id=%s AND owner_id=%s",
                               (draft_id, principal.user_id)).fetchone()
        return self._read(row)

    def save(self, principal: Principal, draft: ShoppingDraft, expected_revision: int) -> None:
        data = self._payload(principal, draft)
        if type(expected_revision) is not int or draft.revision != expected_revision + 1:
            raise RevisionConflict("save must advance exactly one revision")
        with self.database.connect() as conn:
            row = conn.execute("SELECT * FROM icart_drafts WHERE id=%s AND owner_id=%s FOR UPDATE",
                               (draft.id, principal.user_id)).fetchone()
            current = self._read(row)
            if current.revision != expected_revision:
                raise RevisionConflict("draft changed; reload and review the current revision")
            if current.confirmed:
                raise ValidationError("confirmed drafts are closed")
            if current.created_at != draft.created_at:
                raise ValidationError("draft corrections cannot change the time anchor")
            if draft.confirmed:
                # Recheck against the locked current version and database time.
                # An edit and an approval cannot be smuggled into the same save.
                now = conn.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
                approved = confirm_draft(current, principal, expected_revision, now)
                if approved != draft:
                    raise ValidationError("confirmation must approve the unchanged current draft")
            updated = conn.execute("""UPDATE icart_drafts SET revision=%s,state=%s,payload=%s,
                updated_at=clock_timestamp() WHERE id=%s AND owner_id=%s AND revision=%s RETURNING id""",
                (draft.revision, draft.state.value, Jsonb(data), draft.id, principal.user_id, expected_revision)).fetchone()
            if updated is None:
                raise RevisionConflict("draft changed during save")
