from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RUNTIME_FILES = (
    ROOT / "clearledgr" / "api" / "gmail_extension.py",
    ROOT / "clearledgr" / "api" / "ops.py",
    ROOT / "clearledgr" / "services" / "agent_background.py",
    ROOT / "ui" / "slack" / "app.py",
)

FORBIDDEN_TOKENS = (
    "from clearledgr.services.agent_orchestrator import get_orchestrator",
    "get_orchestrator(",
)


def test_runtime_callsites_no_longer_depend_on_legacy_orchestrator():
    for file_path in RUNTIME_FILES:
        content = file_path.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            assert token not in content, f"{token} must not appear in {file_path}"


def test_legacy_orchestrator_module_removed_from_repo():
    legacy_module = ROOT / "clearledgr" / "services" / "agent_orchestrator.py"
    assert not legacy_module.exists(), "legacy agent_orchestrator module must be removed"
