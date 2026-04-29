"""Bank statement parsers (Wave 2 / C6).

Two formats covered:

  * **CAMT.053** — ISO 20022 XML; the standard the EU SEPA banks
    deliver bank-to-corporate statements in. Parsed with stdlib
    xml.etree (no external dep).
  * **OFX** — Open Financial Exchange; the de-facto US/Africa /
    legacy-bank format, served as either SGML or XML. We accept both
    by stripping the SGML preamble and running an XML pass.

Each parser returns the same canonical shape:

    {
        "format": "camt.053" | "ofx",
        "statement": {
            "iban": str | None,
            "account": str | None,
            "currency": str | None,
            "from_date": str | None,        # ISO date
            "to_date": str | None,
            "opening_balance": float | None,
            "closing_balance": float | None,
        },
        "lines": [
            {
                "line_index": int,
                "value_date": str | None,
                "booking_date": str | None,
                "amount": float,                 # signed; outflow negative
                "currency": str,
                "description": str | None,
                "counterparty": str | None,
                "counterparty_iban": str | None,
                "bank_reference": str | None,
                "end_to_end_id": str | None,
            },
            ...
        ],
    }

Outflows (debits leaving our account) are signed negative — they're
what the matcher pairs against ``payment_confirmations`` rows.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── CAMT.053 ────────────────────────────────────────────────────────


_CAMT_NS_RE = re.compile(r"^\{[^}]+\}")


def _strip_ns(tag: str) -> str:
    return _CAMT_NS_RE.sub("", tag)


def _find(elem: Optional[ET.Element], path: List[str]) -> Optional[ET.Element]:
    """Walk a path of localnames through namespaced XML."""
    if elem is None:
        return None
    cur = elem
    for step in path:
        nxt = None
        for child in list(cur):
            if _strip_ns(child.tag) == step:
                nxt = child
                break
        if nxt is None:
            return None
        cur = nxt
    return cur


def _findall(elem: Optional[ET.Element], path: List[str]) -> List[ET.Element]:
    """Collect all descendants matching the final localname after the
    fixed path of localnames."""
    if elem is None:
        return []
    if not path:
        return [elem]
    if len(path) == 1:
        return [
            c for c in list(elem)
            if _strip_ns(c.tag) == path[0]
        ]
    head, tail = path[0], path[1:]
    out: List[ET.Element] = []
    for child in list(elem):
        if _strip_ns(child.tag) == head:
            out.extend(_findall(child, tail))
    return out


def _text(elem: Optional[ET.Element]) -> Optional[str]:
    if elem is None or elem.text is None:
        return None
    s = elem.text.strip()
    return s or None


def _parse_amount_with_sign(entry: ET.Element) -> Optional[float]:
    """CAMT entries have <Amt Ccy="EUR">123.45</Amt> + <CdtDbtInd>DBIT|CRDT</CdtDbtInd>.
    Returns a signed float (DBIT = negative outflow)."""
    amt_el = _find(entry, ["Amt"])
    cd_el = _find(entry, ["CdtDbtInd"])
    if amt_el is None or amt_el.text is None:
        return None
    try:
        amount = float(amt_el.text)
    except ValueError:
        return None
    sign = -1.0 if (cd_el is not None and (cd_el.text or "").strip() == "DBIT") else 1.0
    return amount * sign


def parse_camt053(content: bytes) -> Dict[str, Any]:
    """Parse a single CAMT.053 statement XML payload."""
    text = content.decode("utf-8", errors="replace") if content else ""
    if not text.strip():
        return {"format": "camt.053", "statement": {}, "lines": []}
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        logger.warning("camt.053 parse failed: %s", exc)
        return {"format": "camt.053", "statement": {}, "lines": []}

    # Document/BkToCstmrStmt/Stmt
    stmt = _find(root, ["BkToCstmrStmt", "Stmt"])
    if stmt is None:
        # Some banks wrap differently — try root → Stmt.
        stmt = _find(root, ["Stmt"])
    if stmt is None:
        return {"format": "camt.053", "statement": {}, "lines": []}

    iban = _text(_find(stmt, ["Acct", "Id", "IBAN"]))
    other = _text(_find(stmt, ["Acct", "Id", "Othr", "Id"]))
    currency = _text(_find(stmt, ["Acct", "Ccy"]))

    from_date = _text(_find(stmt, ["FrToDt", "FrDtTm"]))
    to_date = _text(_find(stmt, ["FrToDt", "ToDtTm"]))

    opening = None
    closing = None
    for bal in _findall(stmt, ["Bal"]):
        cd_el = _find(bal, ["Tp", "CdOrPrtry", "Cd"])
        amt_el = _find(bal, ["Amt"])
        if amt_el is None or amt_el.text is None:
            continue
        try:
            value = float(amt_el.text)
        except ValueError:
            continue
        cd = (cd_el.text or "").strip() if cd_el is not None else ""
        if cd in ("OPBD", "PRCD"):
            opening = value
        elif cd in ("CLBD",):
            closing = value

    lines: List[Dict[str, Any]] = []
    for idx, ntry in enumerate(_findall(stmt, ["Ntry"])):
        amount = _parse_amount_with_sign(ntry)
        if amount is None:
            continue
        amt_el = _find(ntry, ["Amt"])
        line_currency = (
            amt_el.attrib.get("Ccy") if amt_el is not None else None
        ) or currency
        if not line_currency:
            continue

        booking_date = _text(_find(ntry, ["BookgDt", "Dt"]))
        value_date = _text(_find(ntry, ["ValDt", "Dt"]))
        bank_ref = _text(_find(ntry, ["AcctSvcrRef"]))

        # Drill into the entry details for counterparty + end-to-end-id.
        ntry_dtls = _find(ntry, ["NtryDtls", "TxDtls"])
        end_to_end = _text(
            _find(ntry_dtls, ["Refs", "EndToEndId"])
        ) if ntry_dtls is not None else None
        # Counterparty: for an outflow (DBIT), the counterparty is the
        # creditor; for an inflow (CRDT), it's the debtor.
        counterparty = None
        counterparty_iban = None
        if ntry_dtls is not None:
            related = _find(ntry_dtls, ["RltdPties"])
            if related is not None:
                for who in ("Cdtr", "Dbtr"):
                    party = _find(related, [who])
                    if party is not None:
                        counterparty = _text(_find(party, ["Nm"])) or counterparty
                acct = _find(related, ["CdtrAcct", "Id", "IBAN"])
                if acct is None:
                    acct = _find(related, ["DbtrAcct", "Id", "IBAN"])
                counterparty_iban = _text(acct)
        description = _text(_find(ntry, ["AddtlNtryInf"]))

        lines.append({
            "line_index": idx,
            "value_date": value_date,
            "booking_date": booking_date,
            "amount": amount,
            "currency": line_currency,
            "description": description,
            "counterparty": counterparty,
            "counterparty_iban": counterparty_iban,
            "bank_reference": bank_ref,
            "end_to_end_id": end_to_end,
        })

    return {
        "format": "camt.053",
        "statement": {
            "iban": iban,
            "account": other or iban,
            "currency": currency,
            "from_date": from_date,
            "to_date": to_date,
            "opening_balance": opening,
            "closing_balance": closing,
        },
        "lines": lines,
    }


# ── OFX ─────────────────────────────────────────────────────────────


_OFX_SELF_CLOSE_RE = re.compile(
    # Match leaf tags only: opening tag, non-empty value, then a
    # newline or another tag. Aggregate tags (with no inline value)
    # already have their closing counterpart in OFX SGML.
    r"<(?P<tag>[A-Z0-9_.]+)>(?P<value>[^<\r\n]+?)\s*(?=\r?\n|<)",
    re.MULTILINE,
)


def _ofx_to_xml(content: bytes) -> str:
    """OFX 1.x is SGML-ish: tags don't always close. Convert to
    well-formed XML by replacing ``<TAG>value`` with ``<TAG>value</TAG>``
    when the next non-whitespace char is a newline or another tag."""
    text = content.decode("utf-8", errors="replace") if content else ""
    ofx_pos = text.find("<OFX")
    if ofx_pos < 0:
        return text
    # Strip the SGML preamble (OFXHEADER:..., DATA:OFXSGML, etc).
    text = text[ofx_pos:]

    def _close(match: re.Match) -> str:
        tag = match.group("tag")
        value = match.group("value")
        return f"<{tag}>{value}</{tag}>"

    fixed = _OFX_SELF_CLOSE_RE.sub(_close, text)
    return fixed


def parse_ofx(content: bytes) -> Dict[str, Any]:
    xml_text = _ofx_to_xml(content)
    if not xml_text.strip():
        return {"format": "ofx", "statement": {}, "lines": []}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("ofx parse failed: %s", exc)
        return {"format": "ofx", "statement": {}, "lines": []}

    # OFX bank statements: BANKMSGSRSV1 / STMTTRNRS / STMTRS / { BANKACCTFROM, BANKTRANLIST, LEDGERBAL, AVAILBAL }
    stmt = _find(root, ["BANKMSGSRSV1", "STMTTRNRS", "STMTRS"])
    if stmt is None:
        return {"format": "ofx", "statement": {}, "lines": []}

    currency = _text(_find(stmt, ["CURDEF"]))
    acct = _find(stmt, ["BANKACCTFROM"])
    account_id = _text(_find(acct, ["ACCTID"]))

    tranlist = _find(stmt, ["BANKTRANLIST"])
    from_date = _text(_find(tranlist, ["DTSTART"]))
    to_date = _text(_find(tranlist, ["DTEND"]))

    ledger_bal = None
    closing_el = _find(stmt, ["LEDGERBAL", "BALAMT"])
    if closing_el is not None and closing_el.text is not None:
        try:
            ledger_bal = float(closing_el.text)
        except ValueError:
            ledger_bal = None

    lines: List[Dict[str, Any]] = []
    for idx, txn in enumerate(_findall(tranlist, ["STMTTRN"])):
        amt_el = _find(txn, ["TRNAMT"])
        if amt_el is None or amt_el.text is None:
            continue
        try:
            amount = float(amt_el.text)
        except ValueError:
            continue
        if not currency:
            continue
        booking_date = _text(_find(txn, ["DTPOSTED"]))
        value_date = _text(_find(txn, ["DTAVAIL"])) or booking_date
        description = (
            _text(_find(txn, ["MEMO"]))
            or _text(_find(txn, ["NAME"]))
        )
        bank_ref = _text(_find(txn, ["FITID"]))
        check_num = _text(_find(txn, ["CHECKNUM"]))
        counterparty = _text(_find(txn, ["NAME"]))

        lines.append({
            "line_index": idx,
            "value_date": value_date,
            "booking_date": booking_date,
            "amount": amount,
            "currency": currency,
            "description": description,
            "counterparty": counterparty,
            "counterparty_iban": None,
            "bank_reference": bank_ref or check_num,
            "end_to_end_id": check_num,
        })

    return {
        "format": "ofx",
        "statement": {
            "iban": None,
            "account": account_id,
            "currency": currency,
            "from_date": from_date,
            "to_date": to_date,
            "opening_balance": None,
            "closing_balance": ledger_bal,
        },
        "lines": lines,
    }


def detect_and_parse(content: bytes, *, filename: str = "") -> Dict[str, Any]:
    """Auto-detect format from content + filename and dispatch."""
    head = content[:512].decode("utf-8", errors="replace").lower() if content else ""
    if "<bktocstmrstmt" in head or "camt.053" in head:
        return parse_camt053(content)
    if "ofxheader" in head or "<ofx" in head:
        return parse_ofx(content)
    if filename.lower().endswith(".ofx"):
        return parse_ofx(content)
    if filename.lower().endswith((".xml", ".camt", ".camt053")):
        return parse_camt053(content)
    # Default: try CAMT first (more structured); fall through to OFX
    # if the CAMT parser returns no lines.
    parsed = parse_camt053(content)
    if parsed["lines"]:
        return parsed
    return parse_ofx(content)
