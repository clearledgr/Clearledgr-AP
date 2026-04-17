"""Domain lookalike detection — DESIGN_THESIS.md §8 Fraud Controls.

Closes a specific attack vector the thesis calls out by name:

  "Domain similarity detection flags 'str1pe.com' emails when
   'stripe.com' is in the vendor master."

The existing ``vendor_domain_lock`` service already blocks ``str1pe.com``
from processing (it's not on any vendor's allowlist), but that's the
weaker signal — the AP Manager sees "unknown sender, route to Review
Required" and moves on. The stronger signal the thesis describes is:
*this unknown sender looks deceptively like a trusted vendor*. That's
a fraud alert that deserves its own reason code, its own audit
entry, and AP Manager attention, not a generic onboarding prompt.

Three detection layers, ordered by fraud-signal strength:

  1. Homoglyph substitution — "str1pe.com" vs "stripe.com". Characters
     that look alike (1/l/i, 0/o, rn/m, vv/w, latin/cyrillic 'a/е/о')
     are normalized to a canonical form; exact match after
     normalization means this is almost certainly an impersonation.

  2. TLD swap — "stripe.co" vs "stripe.com". Same SLD, different TLD.
     Common attacker pattern because the SLD lookup is a cheap typo
     for the user to miss.

  3. Edit distance — "stripes.com" or "strpe.com" vs "stripe.com".
     Damerau-Levenshtein distance ≤ 2 on the registrable base. The
     distance ceiling catches single-character typos + one
     transposition, which is where the vast majority of lookalike
     attacks live.

Fail-safe: the module never raises. Any unexpected input returns
``None`` — the caller treats "no lookalike detected" as the default,
same as the domain lock's existing behaviour when a sender doesn't
match the allowlist.
"""
from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Homoglyph canonicalisation
# ---------------------------------------------------------------------------

# Map of confusable characters to their ASCII-canonical form. Covers the
# three attack families operators encounter: digit-for-letter (1→l, 0→o),
# latin-cyrillic confusables (а→a — the Cyrillic 'а' U+0430 renders
# identically to Latin 'a' U+0061), and multi-char ligatures
# (rn→m, vv→w) handled separately in the substring pass.
_SINGLE_CHAR_HOMOGLYPHS = {
    # Digit → letter (the classic "str1pe" attack)
    "0": "o",
    "1": "l",
    "3": "e",
    "4": "a",
    "5": "s",
    "7": "t",
    # Cyrillic → Latin (same glyph, different codepoint)
    "\u0430": "a",  # CYRILLIC SMALL LETTER A
    "\u0435": "e",  # CYRILLIC SMALL LETTER IE
    "\u043e": "o",  # CYRILLIC SMALL LETTER O
    "\u0440": "p",  # CYRILLIC SMALL LETTER ER
    "\u0441": "c",  # CYRILLIC SMALL LETTER ES
    "\u0443": "y",  # CYRILLIC SMALL LETTER U
    "\u0445": "x",  # CYRILLIC SMALL LETTER HA
    # Greek → Latin (α→a, ο→o, ε→e, ρ→p)
    "\u03b1": "a",
    "\u03bf": "o",
    "\u03b5": "e",
    "\u03c1": "p",
}

# Multi-char substitutions applied after single-char canonicalisation.
# "rn" → "m" is the canonical homograph attack (depending on font,
# "rn" is visually indistinguishable from "m"); "vv" → "w" is the
# secondary.
_MULTI_CHAR_HOMOGLYPHS = [
    ("rn", "m"),
    ("vv", "w"),
]


def _canonicalize_homoglyphs(domain: str) -> str:
    """Reduce a domain to its homoglyph-canonical form.

    The returned form is lowercase ASCII with confusables mapped to
    their visually-identical Latin counterparts. This is NOT a
    security-grade normalisation (attackers who know the mapping can
    work around it) — it's a detection heuristic that catches the
    common families operators see in the wild.
    """
    if not domain:
        return ""

    # NFKC folds compatibility variants (half-width, full-width, etc.)
    # into their canonical form before we start the homoglyph pass.
    normalized = unicodedata.normalize("NFKC", domain.strip().lower())

    # Single-char substitutions.
    out_chars: list[str] = []
    for ch in normalized:
        out_chars.append(_SINGLE_CHAR_HOMOGLYPHS.get(ch, ch))
    canonical = "".join(out_chars)

    # Multi-char substitutions.
    for src, dst in _MULTI_CHAR_HOMOGLYPHS:
        canonical = canonical.replace(src, dst)

    return canonical


# ---------------------------------------------------------------------------
# Registrable-base extraction
# ---------------------------------------------------------------------------
#
# We compare the SLD.TLD pair, not the full domain. For "billing.stripe.com"
# the registrable base is "stripe.com"; for "mail.acme.co.uk" it's
# "acme.co.uk" (handling the common multi-part TLD case). A proper PSL
# lookup would be more accurate but adds a dependency; the heuristic here
# handles the cases attackers actually use.

_MULTI_PART_TLDS = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk",
    "co.nz", "co.jp", "co.za", "co.in", "com.au", "net.au",
    "com.br", "com.mx", "com.ar", "com.sg", "com.hk",
})


def _registrable_base(domain: str) -> str:
    """Return the registrable (SLD + TLD) portion of a domain."""
    if not domain:
        return ""
    parts = domain.lower().strip().strip(".").split(".")
    if len(parts) < 2:
        return domain
    # Handle ccTLDs like co.uk, com.au.
    last_two = ".".join(parts[-2:])
    if last_two in _MULTI_PART_TLDS and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _split_sld_tld(registrable_base: str) -> tuple[str, str]:
    """Split a registrable base into (SLD, TLD). Multi-part TLDs kept intact."""
    parts = registrable_base.split(".")
    if len(parts) < 2:
        return (registrable_base, "")
    last_two = ".".join(parts[-2:])
    if last_two in _MULTI_PART_TLDS and len(parts) >= 3:
        return (parts[-3], ".".join(parts[-2:]))
    return (parts[-2], parts[-1])


# ---------------------------------------------------------------------------
# Damerau-Levenshtein distance
# ---------------------------------------------------------------------------


def _damerau_levenshtein(a: str, b: str) -> int:
    """Return the edit distance between ``a`` and ``b``, counting
    adjacent-character transposition as a single operation.

    Transposition matters because it's the specific typo pattern
    attackers exploit — "stirpe.com" vs "stripe.com" is one
    transposition, which a human reviewer scanning an inbox row is
    prone to miss. Standard Levenshtein counts that as two edits,
    which would let it slip through a distance-≤1 filter.

    Simple full-matrix implementation. The strings we compare are
    registrable bases (typically 5-20 chars), so O(la*lb) memory is
    trivial and avoids the off-by-one traps in rolling-row variants.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    la, lb = len(a), len(b)
    # Full (la+1) x (lb+1) matrix. Row 0 and column 0 are the
    # Levenshtein base cases.
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j

    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,        # deletion
                d[i][j - 1] + 1,        # insertion
                d[i - 1][j - 1] + cost, # substitution
            )
            # Damerau adjacent transposition.
            if (
                i > 1 and j > 1
                and a[i - 1] == b[j - 2]
                and a[i - 2] == b[j - 1]
            ):
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + cost)

    return d[la][lb]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LookalikeMatch:
    """Result of a lookalike comparison.

    ``suspected_impersonation`` is the trusted domain the sender
    appears to mimic. ``category`` names which detector fired so the
    audit trail explains *why* this was flagged (homoglyph attacks
    deserve a louder response than single-character typos).
    """

    sender_domain: str
    suspected_impersonation: str
    category: str  # "homoglyph" | "tld_swap" | "edit_distance"
    score: int  # Edit distance for edit_distance; 0 for exact after
                # canonicalisation; distance-between-TLDs for tld_swap.


# Edit-distance ceiling. 2 catches single-char typo + adjacent
# transposition (the two patterns that dominate real attacks) while
# rejecting "acme.com" → "apple.com" (distance 5) as coincidental.
_EDIT_DISTANCE_CEILING = 2


def detect_lookalike(
    sender_domain: str,
    trusted_domains: Iterable[str],
) -> Optional[LookalikeMatch]:
    """Return a ``LookalikeMatch`` if ``sender_domain`` is suspiciously
    close to any domain in ``trusted_domains``. Returns ``None`` when
    no trusted domain is a plausible impersonation target.

    Ordered by fraud-signal strength: homoglyph > tld_swap >
    edit_distance. The first match wins, and the audit entry the
    caller produces names the specific category.
    """
    if not sender_domain:
        return None

    sender_base = _registrable_base(sender_domain)
    if not sender_base:
        return None
    sender_canonical = _canonicalize_homoglyphs(sender_base)
    sender_sld, sender_tld = _split_sld_tld(sender_base)

    trusted_list = [t for t in (trusted_domains or ()) if t]
    if not trusted_list:
        return None

    # Pass 1: homoglyph attack. Canonicalise both sides; if they match
    # and the raw strings don't, the sender was using confusable
    # characters to impersonate.
    for trusted in trusted_list:
        trusted_base = _registrable_base(trusted)
        if not trusted_base or trusted_base == sender_base:
            continue
        trusted_canonical = _canonicalize_homoglyphs(trusted_base)
        if trusted_canonical and trusted_canonical == sender_canonical:
            return LookalikeMatch(
                sender_domain=sender_base,
                suspected_impersonation=trusted_base,
                category="homoglyph",
                score=0,
            )

    # Pass 2: TLD swap. Same SLD, different TLD.
    for trusted in trusted_list:
        trusted_base = _registrable_base(trusted)
        if not trusted_base or trusted_base == sender_base:
            continue
        trusted_sld, trusted_tld = _split_sld_tld(trusted_base)
        if (
            sender_sld
            and trusted_sld
            and sender_sld == trusted_sld
            and sender_tld != trusted_tld
        ):
            return LookalikeMatch(
                sender_domain=sender_base,
                suspected_impersonation=trusted_base,
                category="tld_swap",
                score=_damerau_levenshtein(sender_tld, trusted_tld),
            )

    # Pass 3: edit distance. Compare the SLD portion only — matching
    # the TLD independently would flag "acme.io" vs "acme.com" which
    # is either a legitimate multi-domain vendor or already covered by
    # pass 2.
    #
    # Length-gap guard handles the "acme.com" vs "acme-ltd.com" case
    # cleanly: length gap 4 > ceiling 2 → skipped without a substring
    # heuristic that would also miss legitimate typosquat patterns
    # ("stripe" vs "stripes" — plural attacks are edit distance 1).
    best_match: Optional[LookalikeMatch] = None
    best_distance = _EDIT_DISTANCE_CEILING + 1
    for trusted in trusted_list:
        trusted_base = _registrable_base(trusted)
        if not trusted_base or trusted_base == sender_base:
            continue
        trusted_sld, _ = _split_sld_tld(trusted_base)
        if not trusted_sld or not sender_sld:
            continue
        # Length gap > ceiling means edit distance is also > ceiling,
        # short-circuit for clarity. Catches "applesauce" vs "apple"
        # (gap 5) and "my-acme" vs "acme" (gap 3) in one check.
        if abs(len(sender_sld) - len(trusted_sld)) > _EDIT_DISTANCE_CEILING:
            continue
        distance = _damerau_levenshtein(sender_sld, trusted_sld)
        if 0 < distance <= _EDIT_DISTANCE_CEILING and distance < best_distance:
            best_distance = distance
            best_match = LookalikeMatch(
                sender_domain=sender_base,
                suspected_impersonation=trusted_base,
                category="edit_distance",
                score=distance,
            )

    return best_match


def collect_org_trusted_domains(db, organization_id: str) -> list[str]:
    """Collect every trusted sender domain across every vendor profile
    in the org — the set lookalike detection compares against.

    Returns a deduplicated list. Payment-processor domains are
    excluded because they're expected to appear in multiple vendors'
    allowlists and flagging something as "resembles stripe.com" when
    stripe.com legitimately shows up everywhere would produce noise.
    """
    try:
        profiles = db.list_vendor_profiles(organization_id, limit=5000)
    except Exception as exc:
        logger.debug("[lookalike] list_vendor_profiles failed: %s", exc)
        return []

    seen: set[str] = set()
    out: list[str] = []
    # Late import avoids a circular dependency at module load time.
    from clearledgr.services.vendor_domain_lock import PAYMENT_PROCESSOR_DOMAINS

    for profile in profiles or []:
        raw = profile.get("sender_domains") if isinstance(profile, dict) else None
        if isinstance(raw, str):
            try:
                import json
                raw = json.loads(raw)
            except (ValueError, TypeError):
                raw = []
        if not isinstance(raw, list):
            continue
        for entry in raw:
            base = _registrable_base(str(entry or ""))
            if not base or base in seen:
                continue
            if base in PAYMENT_PROCESSOR_DOMAINS:
                continue
            seen.add(base)
            out.append(base)
    return out
