from solden.core.ap_item_resolution import (
    resolve_ap_context,
    resolve_ap_correlation_id,
    resolve_ap_item_reference,
)


class FakeDB:
    def __init__(self):
        self.by_id = {}
        self.by_thread = {}
        self.by_message = {}
        self.invoice_status = {}

    def get_ap_item(self, ap_item_id):
        return self.by_id.get(ap_item_id)

    def get_ap_item_by_thread(self, organization_id, reference_id):
        return self.by_thread.get((organization_id, reference_id))

    def get_ap_item_by_message_id(self, organization_id, reference_id):
        return self.by_message.get((organization_id, reference_id))

    def get_invoice_status(self, reference_id, organization_id=None):
        # M5-supported kwarg + post-codex tightening (M13): when an
        # organization_id is supplied, the lookup is SCOPED to that
        # tenant. Pre-fix this method returned the row regardless of
        # tenant (purely by gmail_id), which let
        # ``resolve_ap_context`` adopt a foreign tenant's org.
        row = self.invoice_status.get(reference_id)
        if row is None:
            return None
        if organization_id is None:
            return row
        if str(row.get("organization_id") or "") != str(organization_id):
            return None
        return row


def test_resolve_ap_context_with_matching_org_returns_item():
    """Caller passes the right org → invoice row resolves, item returns."""
    db = FakeDB()
    db.invoice_status["gmail-msg-1"] = {"organization_id": "org-eu-1"}
    db.by_message[("org-eu-1", "gmail-msg-1")] = {
        "id": "ap-1",
        "organization_id": "org-eu-1",
        "thread_id": "gmail-thread-1",
    }

    org_id, item = resolve_ap_context(db, "org-eu-1", "gmail-msg-1")

    assert org_id == "org-eu-1"
    assert item["id"] == "ap-1"


def test_resolve_ap_context_refuses_cross_tenant_org_swap():
    """M13 tightening: pre-fix this function called
    ``db.get_invoice_status(ref)`` WITHOUT passing organization_id,
    then ADOPTED the matched row's org as the resolved org. A caller
    asking for ``"org-test"`` would silently swap to whichever tenant
    happened to own the thread_id in the DB. That was the cross-
    tenant org-swap landmine. The function now passes the requested
    org through and refuses to adopt a foreign org from the row.
    """
    db = FakeDB()
    db.invoice_status["gmail-msg-1"] = {"organization_id": "org-eu-1"}
    db.by_message[("org-eu-1", "gmail-msg-1")] = {
        "id": "ap-1",
        "organization_id": "org-eu-1",
        "thread_id": "gmail-thread-1",
    }

    # Caller passes a different org. The pre-fix function would have
    # returned ``("org-eu-1", ap-1)`` — silent org swap. Post-fix it
    # returns ``("org-test", None)``.
    org_id, item = resolve_ap_context(db, "org-test", "gmail-msg-1")
    assert org_id == "org-test"
    assert item is None


def test_resolve_ap_item_reference_blocks_foreign_ids_unless_allowed():
    db = FakeDB()
    db.by_id["ap-foreign"] = {"id": "ap-foreign", "organization_id": "org-us-1"}

    assert resolve_ap_item_reference(db, "org-eu-1", "ap-foreign") is None
    assert resolve_ap_item_reference(db, "org-eu-1", "ap-foreign", allow_foreign_id=True)["id"] == "ap-foreign"


def test_resolve_ap_correlation_id_with_matching_org():
    """Same M13 tightening: when caller passes the right org, the
    correlation_id is recovered from the invoice_status fallback path."""
    db = FakeDB()
    db.invoice_status["gmail-thread-1"] = {
        "organization_id": "org-eu-1",
        "metadata": {"correlation_id": "corr-123"},
    }

    correlation_id = resolve_ap_correlation_id(
        db,
        "org-eu-1",
        reference_id="gmail-thread-1",
    )

    assert correlation_id == "corr-123"


def test_resolve_ap_correlation_id_refuses_cross_tenant_lookup():
    """A caller asking with the wrong org gets None, not the foreign
    correlation_id. Pre-fix the function adopted the row's org and
    returned its correlation_id."""
    db = FakeDB()
    db.invoice_status["gmail-thread-1"] = {
        "organization_id": "org-eu-1",
        "metadata": {"correlation_id": "corr-123"},
    }

    correlation_id = resolve_ap_correlation_id(
        db,
        "org-test",
        reference_id="gmail-thread-1",
    )

    assert correlation_id is None
