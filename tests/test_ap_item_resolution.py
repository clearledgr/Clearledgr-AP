from clearledgr.core.ap_item_resolution import (
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

    def get_invoice_status(self, reference_id):
        return self.invoice_status.get(reference_id)


def test_resolve_ap_context_uses_invoice_org_and_message_lookup():
    db = FakeDB()
    db.invoice_status["gmail-msg-1"] = {"organization_id": "org-eu-1"}
    db.by_message[("org-eu-1", "gmail-msg-1")] = {
        "id": "ap-1",
        "organization_id": "org-eu-1",
        "thread_id": "gmail-thread-1",
    }

    org_id, item = resolve_ap_context(db, "default", "gmail-msg-1")

    assert org_id == "org-eu-1"
    assert item["id"] == "ap-1"


def test_resolve_ap_item_reference_blocks_foreign_ids_unless_allowed():
    db = FakeDB()
    db.by_id["ap-foreign"] = {"id": "ap-foreign", "organization_id": "org-us-1"}

    assert resolve_ap_item_reference(db, "org-eu-1", "ap-foreign") is None
    assert resolve_ap_item_reference(db, "org-eu-1", "ap-foreign", allow_foreign_id=True)["id"] == "ap-foreign"


def test_resolve_ap_correlation_id_falls_back_to_invoice_status_metadata():
    db = FakeDB()
    db.invoice_status["gmail-thread-1"] = {
        "organization_id": "org-eu-1",
        "metadata": {"correlation_id": "corr-123"},
    }

    correlation_id = resolve_ap_correlation_id(
        db,
        "default",
        reference_id="gmail-thread-1",
    )

    assert correlation_id == "corr-123"
