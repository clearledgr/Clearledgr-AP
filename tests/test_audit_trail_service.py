from __future__ import annotations

from clearledgr.core.database import ClearledgrDB
from clearledgr.services.audit_trail import AuditEventType, AuditTrailService


def test_audit_trail_service_persists_events_via_shared_audit_store(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_SECRET_KEY", "test-secret-key")
    db = ClearledgrDB(str(tmp_path / "audit-trail.db"))
    db.initialize()

    monkeypatch.setattr("clearledgr.services.audit_trail.get_db", lambda: db)

    writer = AuditTrailService("test-org")
    writer.log(
        invoice_id="thread-1",
        event_type=AuditEventType.RECEIVED,
        summary="Email received from vendor@example.com",
        actor="agent",
        details={"subject": "Invoice INV-1"},
        vendor="Acme Co",
        amount=120.0,
    )

    reader = AuditTrailService("test-org")
    trail = reader.get_trail("thread-1")

    assert trail is not None
    assert trail.invoice_id == "thread-1"
    assert trail.current_status == "received"
    assert trail.vendor == "Acme Co"
    assert trail.events[0].summary == "Email received from vendor@example.com"
    assert trail.events[0].event_type == AuditEventType.RECEIVED
