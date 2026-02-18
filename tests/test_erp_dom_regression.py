from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.append(str(TESTS_ROOT))

from clearledgr.core import database as db_module
from clearledgr.services import browser_agent as browser_agent_module
from erp_dom_regression.profiles import (
    REQUIRED_ERP_POST_COMMANDS,
    STAGED_ERP_DOM_PROFILES,
    ERPDOMProfile,
)


class _DOMNodeCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.nodes: List[Tuple[str, Dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str | None]]) -> None:
        normalized = {str(k or "").lower(): str(v or "") for k, v in attrs}
        self.nodes.append((str(tag or "").lower(), normalized))


_SIMPLE_SELECTOR_RE = re.compile(
    r"""^(?:(?P<tag>[a-zA-Z][\w-]*))?\[(?P<attr>[\w:-]+)(?P<op>\*=|=)['"](?P<value>[^'"]+)['"]\]$"""
)


def _load_nodes(path: Path) -> List[Tuple[str, Dict[str, str]]]:
    parser = _DOMNodeCollector()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser.nodes


def _split_selector_group(value: str) -> List[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _selector_matches(nodes: List[Tuple[str, Dict[str, str]]], selector: str) -> bool:
    token = str(selector or "").strip()
    matched = _SIMPLE_SELECTOR_RE.match(token)
    if not matched:
        return False

    tag = str(matched.group("tag") or "").strip().lower()
    attr = str(matched.group("attr") or "").strip().lower()
    op = str(matched.group("op") or "=").strip()
    value = str(matched.group("value") or "").strip().lower()

    for node_tag, attrs in nodes:
        if tag and node_tag != tag:
            continue
        raw = str(attrs.get(attr) or "")
        normalized_raw = raw.lower()
        if op == "=" and normalized_raw == value:
            return True
        if op == "*=" and value in normalized_raw:
            return True
    return False


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLEARLEDGR_DB_PATH", str(tmp_path / "erp_dom_regression.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("AP_TEMPORAL_ENABLED", "false")
    db_module._DB_INSTANCE = None
    browser_agent_module._SERVICE = None
    db = db_module.get_db()
    db.initialize()
    return db


def _create_item(db, suffix: str) -> Dict[str, str]:
    return db.create_ap_item(
        {
            "invoice_key": f"vendor|regression|{suffix}|100.00|",
            "thread_id": f"thread-{suffix}",
            "message_id": f"msg-{suffix}",
            "subject": "Invoice",
            "sender": "vendor@example.com",
            "vendor_name": "Vendor",
            "amount": 100.0,
            "currency": "USD",
            "invoice_number": f"INV-{suffix}",
            "state": "validated",
            "confidence": 0.95,
            "approval_required": True,
            "organization_id": "default",
            "user_id": "dom-regression",
        }
    )


@pytest.mark.parametrize("profile", STAGED_ERP_DOM_PROFILES, ids=lambda p: p.name)
def test_post_invoice_macro_selectors_cover_staged_profiles(db, profile: ERPDOMProfile):
    service = browser_agent_module.get_browser_agent_service()
    item = _create_item(db, profile.name)
    session = service.create_session(
        organization_id="default",
        ap_item_id=str(item["id"]),
        created_by="dom_regression",
    )
    commands = service._build_macro_commands(  # noqa: SLF001 - intentional regression coverage on selector map.
        session=session,
        macro_name="post_invoice_to_erp",
        params={
            "erp_url": "https://mail.google.com/mail/u/0/#inbox",
            "invoice_number": item["invoice_number"],
            "vendor_name": item["vendor_name"],
            "amount": item["amount"],
            "currency": item["currency"],
        },
        correlation_id="erp-dom-regression",
    )
    command_map = {str(command.get("command_id") or ""): command for command in commands}
    nodes = _load_nodes(profile.fixture_path())

    for command_id in REQUIRED_ERP_POST_COMMANDS:
        assert command_id in command_map, f"missing command `{command_id}` in macro build"
        params = command_map[command_id].get("params") or {}
        selectors: List[str] = []
        if command_id == "macro_post_find_entry":
            selectors.extend(_split_selector_group(params.get("selector")))
        else:
            selector = str(params.get("selector") or "").strip()
            if selector:
                selectors.append(selector)
            selectors.extend(
                str(candidate).strip()
                for candidate in (params.get("selector_candidates") or [])
                if str(candidate).strip()
            )
        assert selectors, f"command `{command_id}` has no selectors to validate"
        assert any(_selector_matches(nodes, selector) for selector in selectors), (
            f"profile `{profile.name}` does not satisfy any selector for `{command_id}`: {selectors}"
        )


def test_regression_profiles_have_fixture_files():
    for profile in STAGED_ERP_DOM_PROFILES:
        assert profile.fixture_path().exists(), f"missing fixture for profile `{profile.name}`"
