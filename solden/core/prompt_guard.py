"""Prompt injection detection for untrusted text fields.

Per DESIGN_THESIS.md §8, an invoice whose subject, vendor name, email
body, line-item descriptions, or attachment text contains adversarial
"ignore previous instructions" content is treated as an attempted
manipulation and REJECTED at the deterministic validation gate. The
invoice never reaches the LLM — the gate blocks it, and the Phase 1.1
enforcement machinery routes it to human review with a
``prompt_injection_detected`` reason code.

This module is a pure detector. There is no "sanitize and continue"
mode. Callers that previously stripped injection patterns and let the
invoice proceed have been removed in favour of fail-closed gate
enforcement.

Usage:
    result = detect_injection(invoice.subject)
    if result.detected:
        add_reason(
            "prompt_injection_detected",
            f"Injection attempt in subject: {', '.join(result.matched_patterns)}",
            severity="error",
        )

    # Pure length discipline (not injection-related):
    clipped = clip_untrusted(long_text, max_length=3000)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Pattern

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pattern catalogue
# ---------------------------------------------------------------------------
#
# Each entry is (label, compiled_pattern). The label is surfaced in the
# audit trail so a reviewer can see which class of attack was detected
# without needing to interpret the raw regex.
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: List[tuple[str, Pattern[str]]] = [
    (
        "instruction_override",
        re.compile(
            r"(?i)(ignore|disregard|forget|override)\s+(all\s+|any\s+)?"
            r"(previous|prior|above|earlier|the\s+following)\s+"
            r"(instructions?|prompts?|rules?|context|guidelines?|constraints?)"
        ),
    ),
    (
        "system_prompt_extraction",
        re.compile(
            r"(?i)(show|print|reveal|output|repeat|display|dump|expose)\s+"
            r"(me\s+)?(your\s+)?(system\s+|initial\s+|original\s+)?"
            r"(prompt|instructions?|rules?|configuration)"
        ),
    ),
    (
        "role_hijacking_new_role",
        re.compile(r"(?i)you\s+are\s+now\s+(a|an|the)\s+"),
    ),
    (
        "role_hijacking_new_persona",
        re.compile(r"(?i)new\s+(role|persona|identity|character|instructions?)"),
    ),
    (
        "xml_message_delimiter_injection",
        re.compile(r"(?i)<\s*/?\s*(system|assistant|user|instructions?)\s*>"),
    ),
    (
        "llama_inst_delimiter_injection",
        re.compile(r"(?i)\[/?INST\]"),
    ),
    (
        "markdown_system_fence_injection",
        re.compile(r"(?i)```\s*(system|instructions?)"),
    ),
    (
        "base64_decode_instruction",
        re.compile(
            r"(?i)(base64|b64)\s*(decode|decoded|encoded|instructions?)"
        ),
    ),
    (
        "approval_command_injection",
        re.compile(
            r"(?i)(auto[- ]?approve|immediately\s+approve|force\s+approval|"
            r"bypass\s+(the\s+)?(validation|approval|check|review))"
        ),
    ),
]


# ---------------------------------------------------------------------------
# Length caps — prompt discipline, not injection handling. Kept here so
# every untrusted-field caller uses the same limits.
# ---------------------------------------------------------------------------

MAX_SUBJECT_LENGTH = 300
MAX_BODY_LENGTH = 3000
MAX_ATTACHMENT_LENGTH = 2000
MAX_VENDOR_NAME_LENGTH = 200
MAX_LINE_ITEM_DESCRIPTION_LENGTH = 500


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InjectionDetection:
    """Result of scanning a single text field for injection patterns."""

    detected: bool
    matched_patterns: List[str] = field(default_factory=list)
    # The text, clipped to the relevant max length. Provided so callers
    # don't have to call clip_untrusted separately — whether or not
    # injection was detected, the clipped version is always safe length-wise.
    clipped_text: str = ""

    def __bool__(self) -> bool:
        return self.detected


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_injection(
    text: str,
    *,
    field_name: str = "text",
    max_length: int = MAX_BODY_LENGTH,
) -> InjectionDetection:
    """Scan an untrusted text field for prompt-injection patterns.

    ``field_name`` is only used in logs/audit — it does not affect the
    detection itself. ``max_length`` controls the clip of the returned
    ``clipped_text``; it does NOT truncate the text BEFORE scanning, so
    injection attempts buried at position 5000 of a 10000-char email
    body are still caught.
    """
    if not text:
        return InjectionDetection(detected=False, matched_patterns=[], clipped_text="")

    matched: List[str] = []
    for label, pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            matched.append(label)

    if matched:
        logger.warning(
            "[PromptGuard] Injection detected in %s: patterns=%s preview=%r",
            field_name,
            matched,
            text[:120],
        )

    clipped = text[:max_length] if len(text) > max_length else text
    return InjectionDetection(
        detected=bool(matched),
        matched_patterns=matched,
        clipped_text=clipped,
    )


def clip_untrusted(text: str, *, max_length: int) -> str:
    """Clip an untrusted text field to a maximum length.

    Pure length discipline — no pattern scanning, no filtering. Use this
    when you have already confirmed via the validation gate that the
    text is not an injection attempt, but you still want to bound the
    number of tokens you pay Claude to read.
    """
    if not text:
        return ""
    return text[:max_length] if len(text) > max_length else text


def scan_invoice_fields(
    subject: str = "",
    vendor_name: str = "",
    email_body: str = "",
    attachment_text: str = "",
    line_item_descriptions: List[str] = None,
) -> List[InjectionDetection]:
    """Convenience scanner for an entire invoice's untrusted surface.

    Returns one ``InjectionDetection`` per field. The caller iterates
    through and adds a ``prompt_injection_detected`` reason code to the
    validation gate for any field that returned detected=True.

    Used by ``invoice_validation._evaluate_deterministic_validation``
    as the single entry point for invoice-level injection scanning.
    """
    results: List[InjectionDetection] = []

    results.append(
        detect_injection(subject or "", field_name="subject", max_length=MAX_SUBJECT_LENGTH)
    )
    results.append(
        detect_injection(
            vendor_name or "", field_name="vendor_name", max_length=MAX_VENDOR_NAME_LENGTH
        )
    )
    results.append(
        detect_injection(email_body or "", field_name="email_body", max_length=MAX_BODY_LENGTH)
    )
    results.append(
        detect_injection(
            attachment_text or "",
            field_name="attachment_text",
            max_length=MAX_ATTACHMENT_LENGTH,
        )
    )
    for idx, desc in enumerate(line_item_descriptions or []):
        results.append(
            detect_injection(
                desc or "",
                field_name=f"line_item_{idx}_description",
                max_length=MAX_LINE_ITEM_DESCRIPTION_LENGTH,
            )
        )

    return results
