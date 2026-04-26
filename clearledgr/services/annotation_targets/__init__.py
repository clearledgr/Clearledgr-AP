"""External state propagation — annotation targets (Gap 5).

Every system that should reflect Box state when it changes
implements :class:`AnnotationTarget`. Concrete targets register
themselves at module-import time and become consumers of outbox
events with target prefix ``annotation:``.

Targets shipped in this package:

* :class:`gmail_label.GmailLabelTarget` — applies finance labels to
  the email thread. Replaces the legacy ``GmailLabelObserver``.
* :class:`netsuite_custom_field.NetSuiteCustomFieldTarget` — writes
  ``custbody_clearledgr_state`` on the Vendor Bill via SuiteTalk REST.
* :class:`sap_z_field.SapZFieldTarget` — writes ``Z_CLEARLEDGR_STATE``
  on the supplier invoice via OData PATCH.
* :class:`customer_webhook.CustomerWebhookTarget` — fires the
  customer's outbound webhook subscriptions.
* :class:`slack_card_update.SlackCardUpdateTarget` — re-renders the
  existing approval card with the new state.

Per-tenant configuration of which targets are active lives in the
versioned :class:`PolicyService` under the ``annotation_targets``
kind, so changes are auditable + replayable.
"""
from clearledgr.services.annotation_targets import base  # noqa: F401
from clearledgr.services.annotation_targets import gmail_label  # noqa: F401
from clearledgr.services.annotation_targets import netsuite_custom_field  # noqa: F401
from clearledgr.services.annotation_targets import sap_z_field  # noqa: F401
from clearledgr.services.annotation_targets import customer_webhook  # noqa: F401
from clearledgr.services.annotation_targets import slack_card_update  # noqa: F401
