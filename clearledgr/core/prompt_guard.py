"""Prompt injection guardrails for untrusted text sent to LLMs.

Sanitizes user/external content (invoice emails, attachment text) before
it is interpolated into system or user prompts.  Defence-in-depth: the
goal is to reduce attack surface, not to guarantee prevention.

Applied at:
  - llm_email_parser._build_extraction_prompt  (email body/subject/attachments)
  - ap_decision._build_reasoning_prompt        (invoice subject)
"""

import re
import logging

logger = logging.getLogger(__name__)

# Patterns commonly used in prompt injection attacks
_INJECTION_PATTERNS = [
    # Direct instruction overrides
    re.compile(r"(?i)(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?|context)"),
    # System prompt extraction
    re.compile(r"(?i)(show|print|reveal|output|repeat|display)\s+(your\s+)?(system\s+)?(prompt|instructions?|rules?)"),
    # Role hijacking
    re.compile(r"(?i)you\s+are\s+now\s+(a|an|the)\s+"),
    re.compile(r"(?i)new\s+(role|persona|identity|instructions?)"),
    # Prompt delimiters that try to break out of user content
    re.compile(r"(?i)<\s*/?\s*(system|assistant|user|instructions?)\s*>"),
    re.compile(r"(?i)\[/?INST\]"),
    re.compile(r"(?i)```\s*(system|instructions?)"),
    # Encoding evasion (base64 decode instructions)
    re.compile(r"(?i)(base64|b64)\s*(decode|decode\s+this|encoded)"),
]

# Maximum length for untrusted text fields
_MAX_BODY_LENGTH = 3000
_MAX_SUBJECT_LENGTH = 300
_MAX_ATTACHMENT_LENGTH = 2000


def sanitize_email_body(body: str) -> str:
    """Sanitize an email body before injecting into an LLM prompt.

    Strips known injection patterns and truncates to safe length.
    """
    if not body:
        return ""
    cleaned = _strip_injection_patterns(body)
    return cleaned[:_MAX_BODY_LENGTH]


def sanitize_subject(subject: str) -> str:
    """Sanitize an email subject line."""
    if not subject:
        return ""
    cleaned = _strip_injection_patterns(subject)
    return cleaned[:_MAX_SUBJECT_LENGTH]


def sanitize_attachment_text(text: str) -> str:
    """Sanitize extracted attachment text content."""
    if not text:
        return ""
    cleaned = _strip_injection_patterns(text)
    return cleaned[:_MAX_ATTACHMENT_LENGTH]


def _strip_injection_patterns(text: str) -> str:
    """Remove known prompt injection patterns from text.

    Replaces matches with '[FILTERED]' so the LLM sees that content was
    present but redacted, rather than silently dropping it.
    """
    result = text
    for pattern in _INJECTION_PATTERNS:
        replaced = pattern.sub("[FILTERED]", result)
        if replaced != result:
            logger.warning(
                "[PromptGuard] Injection pattern detected and filtered: %s",
                pattern.pattern[:60],
            )
            result = replaced
    return result
