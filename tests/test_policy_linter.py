"""Coverage for the AP policy linter (Sprint 1).

Tests are pure-function — the linter doesn't touch the DB. Each test
constructs a minimal policy payload, runs the linter, asserts on the
findings list. Keeps the suite fast and the failure mode obvious
when a rule regresses.
"""
from __future__ import annotations

from solden.services import policy_linter as L


def _rules(findings):
    return sorted(f.rule for f in findings)


def test_empty_policy_flags_critical_error():
    findings = L.lint_approval_thresholds({"thresholds": []})
    assert _rules(findings) == ["empty-policy"]
    assert findings[0].severity == L.SEVERITY_ERROR
    assert L.has_errors(findings)


def test_missing_thresholds_key_treated_as_empty():
    # Operators sometimes ship a half-formed payload. Treat as empty.
    findings = L.lint_approval_thresholds({})
    assert _rules(findings) == ["empty-policy"]


def test_clean_two_band_policy_has_no_findings():
    policy = {
        "thresholds": [
            {
                "label": "low",
                "min_amount": 0,
                "max_amount": 1000,
                "approvers": ["ap@acme.com"],
                "auto_approve_below": 100,
            },
            {
                "label": "high",
                "min_amount": 1000,
                "max_amount": None,  # unbounded above
                "approvers": ["cfo@acme.com", "ceo@acme.com"],
            },
        ],
    }
    findings = L.lint_approval_thresholds(policy)
    assert findings == []


def test_unreachable_band_when_min_geq_max():
    policy = {
        "thresholds": [
            {
                "label": "broken",
                "min_amount": 5000,
                "max_amount": 1000,  # inverted
                "approvers": ["x@acme.com"],
            },
            {  # second band so policy isn't otherwise empty
                "label": "tail",
                "min_amount": 0,
                "max_amount": None,
                "approvers": ["y@acme.com"],
            },
        ],
    }
    findings = L.lint_approval_thresholds(policy)
    assert "unreachable-band" in _rules(findings)


def test_no_approvers_band_flags_error():
    policy = {
        "thresholds": [{
            "label": "stuck",
            "min_amount": 0,
            "max_amount": None,
            "approvers": [],
            # no auto_approve_below either
        }],
    }
    findings = L.lint_approval_thresholds(policy)
    assert "no-approvers" in _rules(findings)


def test_no_approvers_ok_when_auto_approve_covers_band():
    policy = {
        "thresholds": [{
            "label": "small-auto",
            "min_amount": 0,
            "max_amount": 500,
            "approvers": [],
            "auto_approve_below": 500,
        }],
    }
    findings = L.lint_approval_thresholds(policy)
    # ``auto_approve_below`` == ``max_amount`` triggers
    # ``auto-approve-risky`` (every invoice in the band auto-approves)
    # but not ``no-approvers``. Both are intentional.
    rules = _rules(findings)
    assert "no-approvers" not in rules


def test_auto_approve_dead_when_below_min():
    policy = {
        "thresholds": [{
            "label": "dead-flag",
            "min_amount": 1000,
            "max_amount": 5000,
            "approvers": ["m@acme.com"],
            "auto_approve_below": 500,  # below min_amount → never fires
        }],
    }
    findings = L.lint_approval_thresholds(policy)
    assert "auto-approve-dead" in _rules(findings)
    dead = next(f for f in findings if f.rule == "auto-approve-dead")
    assert dead.severity == L.SEVERITY_WARNING


def test_auto_approve_risky_when_at_or_above_max():
    policy = {
        "thresholds": [{
            "label": "rubber-stamp",
            "min_amount": 0,
            "max_amount": 10_000,
            "approvers": ["m@acme.com"],
            "auto_approve_below": 10_000,  # every invoice in the band auto-approves
        }],
    }
    findings = L.lint_approval_thresholds(policy)
    risky = [f for f in findings if f.rule == "auto-approve-risky"]
    assert risky and risky[0].severity == L.SEVERITY_ERROR


def test_auto_approve_risky_warning_above_default_ceiling():
    policy = {
        "thresholds": [
            {
                "label": "small",
                "min_amount": 0,
                "max_amount": 1000,
                "approvers": ["ap@acme.com"],
            },
            {
                "label": "large",
                "min_amount": 1000,
                "max_amount": 50_000,
                "approvers": ["cfo@acme.com"],
                "auto_approve_below": 10_000,  # above default ceiling 5000
            },
        ],
    }
    findings = L.lint_approval_thresholds(policy)
    risky = [f for f in findings if f.rule == "auto-approve-risky"]
    assert risky and risky[0].severity == L.SEVERITY_WARNING


def test_auto_approve_risky_ceiling_override():
    # Same payload, but raise the ceiling — should not warn.
    policy = {
        "thresholds": [
            {
                "label": "small",
                "min_amount": 0,
                "max_amount": 1000,
                "approvers": ["ap@acme.com"],
            },
            {
                "label": "large",
                "min_amount": 1000,
                "max_amount": 50_000,
                "approvers": ["cfo@acme.com"],
                "auto_approve_below": 10_000,
            },
        ],
    }
    findings = L.lint_approval_thresholds(policy, auto_approve_ceiling=20_000)
    assert "auto-approve-risky" not in _rules(findings)


def test_duplicate_label_warns():
    policy = {
        "thresholds": [
            {"label": "tier1", "min_amount": 0, "max_amount": 1000, "approvers": ["a@x.com"]},
            {"label": "tier1", "min_amount": 1000, "max_amount": None, "approvers": ["b@x.com"]},
        ],
    }
    findings = L.lint_approval_thresholds(policy)
    dupes = [f for f in findings if f.rule == "duplicate-label"]
    assert dupes and dupes[0].severity == L.SEVERITY_WARNING


def test_redundant_require_all_with_single_approver():
    policy = {
        "thresholds": [{
            "label": "tier1",
            "min_amount": 0,
            "max_amount": None,
            "approvers": ["only@x.com"],
            "require_all": True,
        }],
    }
    findings = L.lint_approval_thresholds(policy)
    redundant = [f for f in findings if f.rule == "redundant-require-all"]
    assert redundant and redundant[0].severity == L.SEVERITY_INFO


def test_coverage_gap_between_bands():
    policy = {
        "thresholds": [
            {"label": "low", "min_amount": 0, "max_amount": 100, "approvers": ["a@x.com"]},
            {"label": "high", "min_amount": 500, "max_amount": None, "approvers": ["b@x.com"]},
            # Gap: $100 -> $500 covers nothing.
        ],
    }
    findings = L.lint_approval_thresholds(policy)
    gaps = [f for f in findings if f.rule == "coverage-gap"]
    assert gaps
    # Message format includes the floats — accept either ``100`` or
    # ``100.0`` style.
    assert any("100" in g.message and "500" in g.message for g in gaps)


def test_coverage_tail_gap_when_no_unbounded_band():
    policy = {
        "thresholds": [{
            "label": "small-only",
            "min_amount": 0,
            "max_amount": 1000,
            "approvers": ["a@x.com"],
        }],
    }
    findings = L.lint_approval_thresholds(policy)
    gaps = [f for f in findings if f.rule == "coverage-gap"]
    # Tail gap: amounts >= 1000 fall through.
    assert any("1000" in g.message and "∞" in g.message for g in gaps)


def test_coverage_skips_vendor_restricted_bands():
    """Vendor-restricted bands aren't part of the global coverage
    chain — they only fire for specific vendors. The walker should
    skip them when calculating gaps so a per-vendor exemption band
    doesn't make the whole policy look broken.
    """
    policy = {
        "thresholds": [
            {
                "label": "stripe-special",
                "min_amount": 0,
                "max_amount": None,
                "approvers": ["finance@x.com"],
                "vendors": ["Stripe Inc"],
            },
            {
                "label": "org-test",
                "min_amount": 0,
                "max_amount": None,
                "approvers": ["ap@x.com"],
            },
        ],
    }
    findings = L.lint_approval_thresholds(policy)
    assert "coverage-gap" not in _rules(findings)


def test_findings_are_sorted_by_severity_then_location():
    """Ordering matters for stable CI output. Errors first, then
    warnings, then info; ties broken by location.
    """
    policy = {
        "thresholds": [
            {
                "label": "tier1",
                "min_amount": 0,
                "max_amount": None,
                "approvers": ["only@x.com"],
                "require_all": True,
            },  # info: redundant-require-all
            {
                "label": "tier1",  # duplicate label → warning
                "min_amount": 5000,
                "max_amount": 1000,  # inverted → error
                "approvers": ["other@x.com"],
            },
        ],
    }
    findings = L.lint_approval_thresholds(policy)
    severities = [f.severity for f in findings]
    # Errors should appear before warnings before info.
    error_idx = [i for i, s in enumerate(severities) if s == L.SEVERITY_ERROR]
    warn_idx = [i for i, s in enumerate(severities) if s == L.SEVERITY_WARNING]
    info_idx = [i for i, s in enumerate(severities) if s == L.SEVERITY_INFO]
    if error_idx and warn_idx:
        assert max(error_idx) < min(warn_idx)
    if warn_idx and info_idx:
        assert max(warn_idx) < min(info_idx)


def test_has_errors_helper():
    assert L.has_errors([])  is False
    assert L.has_errors([L.LintFinding("x", L.SEVERITY_WARNING, "m", "p")]) is False
    assert L.has_errors([L.LintFinding("x", L.SEVERITY_ERROR, "m", "p")])  is True
