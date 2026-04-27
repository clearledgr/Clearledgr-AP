# Clearledgr Security & Compliance Packet

This directory is the canonical evidence packet for enterprise security
review of Clearledgr. It is the answer Mo sends prospects when they ask
"are you SOC2?" before we have a Type 1 attestation in hand.

## What's in here

| File | Audience | Purpose |
|---|---|---|
| [CONTROLS.md](CONTROLS.md) | Security teams, auditors | Trust-Service-Criteria mapping with code:line citations — every control claim points at the actual implementation. |
| [SUB_PROCESSORS.md](SUB_PROCESSORS.md) | DPO, legal, GDPR reviewers | Authoritative list of every third party that processes customer data. |
| [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md) | Customer security teams | How Clearledgr detects, contains, communicates, and learns from incidents. |
| [VULNERABILITY_DISCLOSURE.md](VULNERABILITY_DISCLOSURE.md) | Researchers, white-hats | Coordinated disclosure policy + scope + reward signal. |
| [DPA.md](DPA.md) | Customer legal | GDPR-aligned Data Processing Addendum. Customer-signable. |
| [SECURITY_QUESTIONNAIRE.md](SECURITY_QUESTIONNAIRE.md) | Sales engineers | Pre-filled answers to the 80% of SIG/CAIQ questions that recur. |

## Attestation status

Clearledgr does **not** hold SOC2 Type 1 or Type 2 attestation today. The
controls below are implemented and operating; the third-party audit is
scheduled (auditor selection in progress; expected Type 1 report ~6
weeks after engagement). For prospects that require attestation as a
hard gate, this packet documents the equivalent controls and our path
to attestation.

## How to use this packet

**For sales:** zip the entire `docs/security/` directory and send it to
prospects who ask for SOC2 evidence. Pair with the live
[Privacy Policy](https://workspace.clearledgr.com/privacy) and
[Terms](https://workspace.clearledgr.com/terms).

**For prospects:** every control in [CONTROLS.md](CONTROLS.md) cites a
specific file:line in the source tree. The repo is auditable on request
under NDA.

**For internal eng:** if you ship a control change (rotate a key,
remove a sub-processor, etc.), update the relevant doc in the same PR.
Stale claims here are worse than missing claims — they are misleading.
