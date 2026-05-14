"""AP policy linter — static analysis on ``approval_thresholds``.

Where the M22-era ``policy_service`` validates that a policy *parses*,
the linter validates that the policy *makes sense*. Same shape as a
shellcheck / eslint pass: rule-based, severity-tagged, line-grained.

Rules ship in v0.1 (Sprint 1, ModernRelay-inspired roadmap):

* ``empty-policy``         — no threshold bands; routes nothing
* ``coverage-gap``         — bands don't span $0 → ∞ contiguously
* ``unreachable-band``     — band where ``min_amount >= max_amount``
* ``no-approvers``         — band has no approvers and no
                              ``auto_approve_below``
* ``auto-approve-risky``   — ``auto_approve_below`` near or above
                              the band's ``max_amount``
* ``auto-approve-dead``    — ``auto_approve_below <= min_amount``
                              so the flag never fires
* ``duplicate-label``      — two bands share the same routing label
* ``redundant-require-all``— ``require_all=true`` with one approver

Designed to run from the CLI (``solden policy lint``) and as a CI
guard (linter exits non-zero if any ``error``-severity rule fires).
DB-aware checks (e.g. "approver email is a deactivated user") are
deferred to v0.2 — they need org-user lookups and complicate the
pure-function story.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict, Iterable, List, Optional, Sequence


SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


@dataclasses.dataclass(frozen=True)
class LintFinding:
    """One rule firing on one location.

    ``location`` is "thresholds[<index>]" or "policy" for top-level
    rules. ``severity`` decides whether ``solden policy lint`` exits
    non-zero in CI.
    """
    rule: str
    severity: str
    message: str
    location: str
    suggestion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


# Default safety ceiling for ``auto-approve-risky`` warnings. Any
# ``auto_approve_below`` over this is flagged. Operators with explicit
# CFO sign-off can pass ``--auto-approve-ceiling`` to override.
DEFAULT_AUTO_APPROVE_CEILING = 5_000.0


def lint_approval_thresholds(
    content: Dict[str, Any],
    *,
    auto_approve_ceiling: float = DEFAULT_AUTO_APPROVE_CEILING,
) -> List[LintFinding]:
    """Run every v0.1 rule against an ``approval_thresholds`` payload.

    Input shape matches ``services/policy_service.py:_replay_approval_
    thresholds`` — a dict with a ``thresholds`` list of band dicts.

    Returns findings ordered by severity then location for stable
    output. Empty list means the policy is clean.
    """
    findings: List[LintFinding] = []
    bands_raw = content.get("thresholds") if isinstance(content, dict) else None
    bands: List[Dict[str, Any]] = list(bands_raw or [])

    if not bands:
        findings.append(LintFinding(
            rule="empty-policy",
            severity=SEVERITY_ERROR,
            message="Policy has no threshold bands. Every AP item will fall through with no routing.",
            location="policy",
            suggestion="Add at least one band covering $0 → ∞.",
        ))
        return findings

    for idx, band in enumerate(bands):
        if not isinstance(band, dict):
            findings.append(LintFinding(
                rule="empty-policy",
                severity=SEVERITY_ERROR,
                message=f"Band at index {idx} is not an object.",
                location=f"thresholds[{idx}]",
                suggestion="Each band must be a JSON object with min_amount / max_amount / approvers.",
            ))
            continue
        findings.extend(_lint_one_band(idx, band, auto_approve_ceiling))

    findings.extend(_lint_label_collisions(bands))
    findings.extend(_lint_coverage_gaps(bands))

    # Stable ordering: severity (error > warning > info) then location.
    severity_rank = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}
    findings.sort(key=lambda f: (severity_rank.get(f.severity, 99), f.location, f.rule))
    return findings


def _lint_one_band(idx: int, band: Dict[str, Any], auto_approve_ceiling: float) -> List[LintFinding]:
    out: List[LintFinding] = []
    loc = f"thresholds[{idx}]"

    min_amt = _safe_float(band.get("min_amount")) or 0.0
    max_amt = _safe_float(band.get("max_amount"))
    auto_below = _safe_float(band.get("auto_approve_below"))
    approvers = list(band.get("approvers") or [])
    require_all = bool(band.get("require_all"))
    label = _band_label(band, idx)

    # ``unreachable-band``: an inverted or zero-width range never matches.
    if max_amt is not None and min_amt >= max_amt:
        out.append(LintFinding(
            rule="unreachable-band",
            severity=SEVERITY_ERROR,
            message=(
                f"Band {label!r} has min_amount={min_amt} >= max_amount={max_amt}; "
                "no AP item amount can ever match this band."
            ),
            location=loc,
            suggestion=(
                "Either raise max_amount above min_amount or remove this band. "
                "If you want a single-amount route, use min_amount=N and max_amount=N+0.01."
            ),
        ))

    # ``no-approvers``: every band needs either an approver list or an
    # auto-approve threshold that covers the band's range.
    if not approvers and auto_below is None:
        out.append(LintFinding(
            rule="no-approvers",
            severity=SEVERITY_ERROR,
            message=(
                f"Band {label!r} has no approvers and no auto_approve_below; "
                "AP items in this range route to nobody."
            ),
            location=loc,
            suggestion="Add at least one approver email or an auto_approve_below threshold.",
        ))

    if auto_below is not None:
        # ``auto-approve-dead``: auto-threshold is below the band's floor,
        # so it never fires.
        if auto_below <= min_amt:
            out.append(LintFinding(
                rule="auto-approve-dead",
                severity=SEVERITY_WARNING,
                message=(
                    f"Band {label!r} has auto_approve_below={auto_below} <= "
                    f"min_amount={min_amt}; the auto-approve flag will never fire."
                ),
                location=loc,
                suggestion="Raise auto_approve_below above min_amount or remove it.",
            ))

        # ``auto-approve-risky``: auto-threshold reaches the band's ceiling,
        # OR is above the configured safety ceiling.
        if max_amt is not None and auto_below >= max_amt:
            out.append(LintFinding(
                rule="auto-approve-risky",
                severity=SEVERITY_ERROR,
                message=(
                    f"Band {label!r} has auto_approve_below={auto_below} >= "
                    f"max_amount={max_amt}; every invoice in this band auto-approves."
                ),
                location=loc,
                suggestion=(
                    "If you want everything in this band auto-approved, drop the band entirely "
                    "and let the next band's range take over. Otherwise lower auto_approve_below."
                ),
            ))
        elif auto_below > auto_approve_ceiling:
            out.append(LintFinding(
                rule="auto-approve-risky",
                severity=SEVERITY_WARNING,
                message=(
                    f"Band {label!r} has auto_approve_below={auto_below} above the safety ceiling "
                    f"{auto_approve_ceiling}; this auto-approves invoices that may need review."
                ),
                location=loc,
                suggestion=(
                    "Confirm this is intentional with the CFO. Pass --auto-approve-ceiling to "
                    "raise the lint ceiling for this org."
                ),
            ))

    # ``redundant-require-all``: ``require_all=True`` is a no-op when there's
    # only one approver. Hints the operator misunderstood the flag.
    if require_all and len(approvers) <= 1:
        out.append(LintFinding(
            rule="redundant-require-all",
            severity=SEVERITY_INFO,
            message=(
                f"Band {label!r} sets require_all=true but has {len(approvers)} approver(s); "
                "the flag is a no-op."
            ),
            location=loc,
            suggestion="Remove require_all or add additional approvers if joint approval is intended.",
        ))

    return out


def _lint_label_collisions(bands: Sequence[Dict[str, Any]]) -> List[LintFinding]:
    """``duplicate-label``: two bands with the same label make audit
    replay ambiguous (the band that matched first wins, but the audit
    log says only the label, so reconstructing the actual routing
    decision later is fuzzy).
    """
    seen: Dict[str, int] = {}
    out: List[LintFinding] = []
    for idx, band in enumerate(bands):
        if not isinstance(band, dict):
            continue
        label = _band_label(band, idx)
        if label in seen:
            first = seen[label]
            out.append(LintFinding(
                rule="duplicate-label",
                severity=SEVERITY_WARNING,
                message=(
                    f"Bands at index {first} and {idx} share label {label!r}; "
                    "audit replay can't distinguish which routed an invoice."
                ),
                location=f"thresholds[{idx}]",
                suggestion="Give each band a unique label.",
            ))
        else:
            seen[label] = idx
    return out


def _lint_coverage_gaps(bands: Sequence[Dict[str, Any]]) -> List[LintFinding]:
    """``coverage-gap``: bands should span [0, ∞) without gaps. A gap
    means AP items in that amount range fall through with no routing.

    Algorithm: sort bands by ``min_amount``, walk left-to-right, track
    the current ceiling. Each band's ``min_amount`` must be <= the
    running ceiling; otherwise there's a gap. The chain must end at
    a band with ``max_amount=None`` (unbounded) or there's a tail gap.
    """
    out: List[LintFinding] = []
    typed: List[tuple] = []
    for idx, band in enumerate(bands):
        if not isinstance(band, dict):
            continue
        min_amt = _safe_float(band.get("min_amount")) or 0.0
        max_amt = _safe_float(band.get("max_amount"))
        # Vendor-restricted bands don't fill the coverage chain — they
        # only fire for specific vendors. Skip them in the coverage walk.
        if band.get("vendors"):
            continue
        typed.append((idx, min_amt, max_amt))

    if not typed:
        return out

    typed.sort(key=lambda t: t[1])

    ceiling: Optional[float] = 0.0
    for idx, min_amt, max_amt in typed:
        if ceiling is None:
            # Already past an unbounded band — a band after that
            # can't extend coverage.
            break
        if min_amt > ceiling:
            out.append(LintFinding(
                rule="coverage-gap",
                severity=SEVERITY_ERROR,
                message=(
                    f"Coverage gap: no band covers amount range "
                    f"[{ceiling}, {min_amt}). AP items in that range fall through with no routing."
                ),
                location=f"thresholds[{idx}]",
                suggestion=(
                    f"Add a band with min_amount={ceiling} max_amount={min_amt}, "
                    "or extend the previous band's max_amount."
                ),
            ))
        # Advance ceiling. If max_amt is None (unbounded), we're done.
        if max_amt is None:
            ceiling = None
        else:
            ceiling = max(ceiling or 0.0, max_amt)

    if ceiling is not None:
        out.append(LintFinding(
            rule="coverage-gap",
            severity=SEVERITY_ERROR,
            message=(
                f"Coverage gap: no band covers amount range [{ceiling}, ∞). "
                "Large invoices fall through with no routing."
            ),
            location="policy",
            suggestion=(
                f"Add a final band with min_amount={ceiling} and no max_amount "
                "(or max_amount=null) to cover all amounts above this ceiling."
            ),
        ))

    return out


def _band_label(band: Dict[str, Any], idx: int) -> str:
    label = band.get("label") or band.get("name") or band.get("channel")
    return str(label) if label else f"<unnamed-band-{idx}>"


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def has_errors(findings: Iterable[LintFinding]) -> bool:
    """True if any finding is at error severity. Used by the CLI to
    pick the exit code (0 = clean, 1 = error-level findings).
    """
    return any(f.severity == SEVERITY_ERROR for f in findings)
