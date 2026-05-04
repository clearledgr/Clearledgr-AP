"""Forbidden-currency-pattern fence.

Solden launches in EU/UK; "USD" must never be a default for any record-
or render-level value. The earlier currency sweep removed every known
leak. This test pins that and fails CI if a regression sneaks in.

What's checked
--------------
The workspace SPA (``ui/web-app/src/``) and Gmail extension
(``ui/gmail-extension/``) are scanned for:

  1. ``|| 'USD'``  / ``|| "USD"``  — masquerade-as-USD fallbacks
  2. ``'$'`` or ``"$"`` followed by a money interpolation — hardcoded
     dollar prefix
  3. Function-name patterns from the deprecated helpers
     (``fmtDollar``, ``fmtMoney``, ``formatBankAmount``) — replaced by
     the canonical ``formatAmount`` in ``utils/formatters.js``.

What's NOT checked
------------------
Backend Python defaults already classified as configuration seeds
(DDL column defaults that are documented as last-line-of-defense, the
``LocaleSettings.default_currency = "EUR"`` org-locale baseline, USD
appearing in test fixtures as input data). Those are intentional;
record-level corruption was the failure mode.

Why this test exists
--------------------
Before this fence Mo had to spot each USD leak by eye on the live
workspace. Each "fix all" commit consolidated one site while another
was still leaking somewhere else. A regression test that names the
forbidden patterns turns the next slip into a noisy CI failure
instead of a customer-visible "$390" on the dashboard.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

FRONTEND_DIRS = [
    REPO_ROOT / "ui" / "web-app" / "src",
    REPO_ROOT / "ui" / "gmail-extension" / "src",
]

# File paths under FRONTEND_DIRS that are allowed to mention the
# patterns below — almost always the canonical helper itself
# (documenting what it replaces) or test files asserting the patterns
# are absent.
ALLOWLIST_SUFFIXES = (
    "ui/web-app/src/utils/formatters.js",
    "ui/gmail-extension/src/utils/formatters.js",
    "tests/test_no_currency_leaks.py",
)

# (label, regex). Each regex flags one shape of leak.
FORBIDDEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "USD-default fallback (`|| 'USD'`)",
        re.compile(r"""\|\|\s*['"]USD['"]"""),
    ),
    (
        "Hardcoded dollar prefix on a money interpolation",
        re.compile(r"""['"]\$['"]\s*\+\s*(?:Number\(|amount|value|total|v\b)"""),
    ),
    (
        "Deprecated formatter name (use formatAmount instead)",
        re.compile(r"\b(?:fmtDollar|fmtMoney|formatBankAmount)\s*\("),
    ),
    (
        "Deprecated formatter import (use formatAmount instead)",
        re.compile(r"import\b[^;]*\b(?:fmtDollar|fmtMoney|formatBankAmount)\b"),
    ),
]

SCAN_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".html")


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for root in FRONTEND_DIRS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in {"node_modules", "dist", "build"} for part in path.parts):
                continue
            if path.suffix not in SCAN_EXTENSIONS:
                continue
            files.append(path)
    return files


def _is_allowlisted(path: Path) -> bool:
    rel = path.relative_to(REPO_ROOT).as_posix()
    return any(rel.endswith(suffix) for suffix in ALLOWLIST_SUFFIXES)


@pytest.mark.parametrize("label,pattern", FORBIDDEN_PATTERNS)
def test_no_currency_leak(label: str, pattern: re.Pattern[str]) -> None:
    """No file outside the allowlist may match a forbidden pattern."""
    hits: list[str] = []
    for path in _iter_source_files():
        if _is_allowlisted(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                rel = path.relative_to(REPO_ROOT)
                hits.append(f"  {rel}:{line_no}  {line.strip()[:120]}")
    assert not hits, (
        f"Forbidden pattern detected — {label}\n"
        f"Use the canonical `formatAmount(amount, currency, opts?)` from "
        f"`utils/formatters.js`. When currency is missing, render the number "
        f"alone — never fabricate USD.\n"
        f"Hits:\n" + "\n".join(hits)
    )
