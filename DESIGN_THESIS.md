# Product Design Thesis

**Version 1.0 · Accounts Payable**

*This document defines what Clearledgr is, how it is built, how it behaves, and how it must feel to the people who use it. It is the authoritative reference for product, design, and engineering. It should be read before any surface is designed and consulted whenever a product decision is contested.*

*Confidential · Clearledgr Ltd · 2026*

---

## 1. The Reference Point

Before defining what Clearledgr is, it is worth being precise about what we are drawing from. Streak is a CRM built entirely inside Gmail. Not integrated with Gmail — inside it. Users install a browser extension, and Streak modifies Gmail's interface directly: adding pipelines to the left nav, injecting context panels into email threads, placing stage labels on inbox rows, and registering custom page views within Gmail's own chrome. There is no separate URL. There is no new tab. The product lives where the user already lives.

Streak built their product on InboxSDK — a JavaScript library they created and subsequently open-sourced. InboxSDK abstracts Gmail's DOM entirely, providing a high-level API for injecting UI into Gmail without directly manipulating the page structure. Streak uses InboxSDK to build Streak. Other developers use it to build extensions on top of Gmail. Clearledgr will be built on InboxSDK.

The structural insight Streak proved is this: the highest-adoption business tool is the one that requires the least behaviour change. Sales teams adopted Streak because it met them in their inbox. They did not have to log into a new system, migrate their data, or learn a new interface. The CRM appeared inside the tool they were already using eight hours a day.

There is a harder truth underneath this that Aleem Mawani has spent thirteen years navigating: Gmail changes its DOM. Constantly. Browser extensions that manipulate Gmail's HTML directly break every time Google updates the interface — and Google does not announce those changes in advance. Streak's response to this problem is InboxSDK itself. The SDK's implementation is remotely loaded: InboxSDK hosts the full implementation separately from the extension bundle. When Gmail updates its DOM, InboxSDK updates the implementation. Extensions built on InboxSDK keep working without a new Chrome Web Store submission, without a user reinstalling anything, without a support ticket. The user refreshes Gmail and the fix is live.

Clearledgr inherits this architecture by building on InboxSDK. The dependency is real but well-understood. InboxSDK is MIT-licensed and open-source — the code can be forked. The more important dependency is not the codebase but the maintenance work: 13 years of Streak's engineering team tracking Gmail's DOM changes as Google ships them. If Streak reallocates that team, the library does not disappear, but the fix cadence for the next Gmail redesign slows. Clearledgr would inherit the DOM maintenance burden.

The mitigation is operational, not architectural. Three responsibilities sit with the Clearledgr engineering team from day one: track InboxSDK commit velocity and Streak's public signals about the library's roadmap; keep at least one engineer with sufficient Gmail DOM internals knowledge to own a fork if needed; contribute upstream where practical — it builds the relationship and the institutional knowledge simultaneously. Forking is a credible backstop, not a plan A. The goal is to make it unnecessary.

Chrome's Manifest V3 migration is a current constraint, not a future one. InboxSDK publishes an MV3-compatible NPM package that does not rely on remote code loading. Clearledgr must be built on this version from day one. An extension built on the legacy MV2 approach will face forced migration and potential Chrome Web Store removal.

**On Outlook:** Clearledgr V1 is Google Workspace only. InboxSDK does not extend to Outlook; Gmail's NavMenu, sidebar injection, toolbar buttons, and custom routes are all Google infrastructure. Microsoft's extension model is different enough that building for both simultaneously would compromise the depth achievable in Gmail. The V1 boundary is firm. The trigger for the Outlook investment is a defined enterprise demand signal — either a significant revenue threshold crossed in Google Workspace customers, a cluster of qualified Outlook-native enterprise prospects blocked at procurement, or a Series A commitment with Outlook expansion scoped. Until one of those conditions is met, Outlook is explicitly out of scope and stated as such in every enterprise sales conversation before procurement asks.

> *Clearledgr applies the same thesis to finance operations. AP teams already live in email. Vendor invoices arrive by email. Approval requests are sent by email. Vendor queries are resolved by email. The work is already there. Clearledgr makes the work execute itself.*

---

## 2. The Thesis

There is one thing that separates Clearledgr from every other tool built on the Gmail-native thesis, including Streak: Streak surfaces data for humans to act on. Clearledgr acts autonomously and surfaces only what humans need to decide. This is not a vertical application of Streak's model. It is a fundamentally different product philosophy built on the same infrastructure.

Streak is an instrument. It gives sales teams visibility into their pipeline so they can make decisions. The human is always the actor. The CRM is always the record. Clearledgr is an agent. It makes decisions, executes actions, writes to the ERP, schedules payments, chases vendors, and flags exceptions — all without human instruction on each step. The human is the approver of last resort, not the operator of first resort. That distinction changes everything: the interface, the trust model, the expansion path, and the commercial conversation.

> *Streak removes friction from human work. Clearledgr replaces human work entirely — except at the margin, where judgment is genuinely required.*

Finance work already happens in email. Every invoice arrives as an attachment. Every approval is a reply chain. Every exception generates a thread. Every vendor query lands in someone's inbox and waits for a human to remember it. The existing tools — ERPs, AP portals, workflow software — all ask finance teams to leave that email, open another system, manually re-enter data that was already in the attachment, and remember to go back and update status. The friction is not a bug. It is the product.

Clearledgr removes all of it. The agent lives inside Gmail. It reads every incoming invoice, extracts the data, runs the 3-way match against the ERP, routes the approval to the right person, and logs every action with its reasoning — without being asked. The finance team sees what genuinely needs them. Everything else is already done.

The Streak reference point is correct for one dimension: the surface. Gmail is the right place to build because that is where the work already happens. But Streak built a CRM. Clearledgr builds an agent. The surface is shared. The philosophy is not.

> *The product is not software that finance teams use. It is the intelligence layer through which finance work gets done — running silently inside the tool they already open first thing every morning.*

---

## 3. What We Are Building

Clearledgr is the agentic execution layer for finance operations. V1 is Accounts Payable. The agent handles invoice ingestion, data extraction, 3-way matching, exception flagging, approval routing, ERP posting, and payment scheduling — autonomously, from inside Gmail, without requiring any behaviour change from the finance team.

V1 also includes Vendor Onboarding as a direct dependency of AP. You cannot pay a vendor who is not in your system. The agent handles the entire onboarding journey: invite, KYC document collection, bank account verification, and ERP activation.

### The Platform Opportunity

The constraint on Clearledgr's scope is not opportunity — it is sequencing. Nearly every finance workflow currently happens over email. That is the surface Clearledgr owns. The question is not which workflows are addressable. It is which ones to enter first, in which order, and how to use each one to earn the right to the next.

The full opportunity spans six domains of finance operations. Every workflow listed below currently runs over email, involves repetitive human steps that can be automated, and generates data that makes the agent smarter for every adjacent workflow.

### Core Finance Operations

This is the primary expansion path — the sequence every Clearledgr customer will move through as trust accumulates.

| Stage | Workflow | What the agent handles |
|-------|----------|------------------------|
| 01 | Accounts Payable | The wedge. Invoice ingestion, data extraction, 3-way match, exception flagging, approval routing, ERP posting, payment scheduling. |
| 02 | Vendor Onboarding | AP dependency. Invite dispatch, KYC document collection, bank account verification via micro-deposit, ERP activation. |
| 03 | Accounts Receivable | Mirror of AP — same infrastructure, reversed direction. Invoice generation, collections, aging management, dispute tracking, dunning escalation. |
| 04 | Reconciliation | Transaction matching across AP and AR. Agent holds both sides of the ledger. Bank reconciliation, intercompany matching, variance surfacing. |
| 05 | Month-End Close | Checklist automation, blocker identification, close compression. Controller-level value. Target: compress a 10-day close to 3 days. |
| 06 | Flux Analysis | Variance explanation from accumulated transaction data. Agent explains why numbers moved — not just reports that they did. CFO-level value. |
| 07 | FP&A and Budgeting | Forward-looking models built on historical transaction data the agent has been accumulating since day one. Scenario modelling, budget vs actual, reforecast. |
| 08 | Audit and Compliance | The audit trail built from day one of AP becomes the compliance product. Every action logged, every approval recorded, every exception rationale preserved. |

### Treasury and Cash Management

Once Clearledgr owns AP and AR, it holds the data required to run treasury workflows that today require dedicated treasury teams or expensive specialist software.

| Workflow | Agent scope | What it unlocks |
|----------|-------------|-----------------|
| Cash flow forecasting | Agent projects inflows and outflows from live AP and AR data. No spreadsheet, no manual input. | Finance teams see 30/60/90-day cash position in Gmail without a separate treasury tool. |
| Payment run optimisation | Agent identifies early payment discount opportunities, batches payments by bank, flags FX exposure on cross-border invoices. | Measurable cost reduction from captured discounts. Direct ROI visible in AP data. |
| Dynamic discounting | Agent surfaces invoices where early payment earns a discount and routes the decision to the CFO with a calculated return figure. | AP becomes a yield-generating activity, not just a cost centre. |
| Bank reconciliation | Agent matches ERP transactions to bank statement entries. Surfaces unmatched items with context from the Box timeline. | Eliminates the monthly bank rec spreadsheet entirely. |

### Procurement and Spend

Procurement workflows are almost entirely email-native. PO approvals, vendor quotes, contract negotiations, and spend reviews all run through inboxes. Clearledgr's agent is already reading vendor emails — extending into procurement is a natural surface expansion.

| Workflow | Agent scope | What it unlocks |
|----------|-------------|-----------------|
| PO creation and approval | Agent drafts POs from approved requisitions, routes for approval by policy, posts to ERP on sign-off. | Procurement cycle time drops from days to hours. PO coverage improves, reducing AP exceptions. |
| Spend analysis | Agent categorises every invoice by vendor, category, cost centre, and period. Surfaces concentration risk, anomalies, and trends. | CFO sees spend intelligence without a BI tool or a data team. |
| Contract expiry tracking | Agent monitors vendor contract dates from email context. Alerts procurement before expiry with renewal lead time. | Eliminates contract lapses that create supply chain risk or pricing disadvantage. |
| Maverick spend detection | Agent flags invoices with no matching PO against policy thresholds. Identifies recurring offenders by cost centre. | Spend policy compliance without manual audit. |

### Payroll and Workforce Costs

Payroll approval, expense processing, and contractor invoice management are high-frequency, policy-governed workflows — exactly the conditions where an agent adds most value.

| Workflow | Agent scope | What it unlocks |
|----------|-------------|-----------------|
| Payroll approval | Agent validates payroll runs against headcount records, flags anomalies, routes for CFO approval, posts to ERP. | Payroll approval cycle reduced from 2 days to 2 hours. Audit trail for every run. |
| Expense claim processing | Agent reads expense submissions, validates against policy, flags violations, routes compliant claims for payment. | Finance teams stop manually reviewing low-value receipts. Policy violations caught automatically. |
| Contractor invoicing | Agent processes contractor invoices against SOWs, validates hours and rates, routes for approval, schedules payment. | Contractor payment accuracy improves. Disputes resolved with agent-held evidence from the email thread. |

### Reporting and Control

Once Clearledgr accumulates transaction data across AP, AR, and procurement, it holds more complete financial data than most finance teams can access in real time through their ERP alone.

| Workflow | Agent scope | What it unlocks |
|----------|-------------|-----------------|
| Management accounts | Agent assembles P&L, balance sheet movements, and cash flow from live transaction data. Posts draft to Gmail for Controller review. | Management accounts prepared in hours, not days. Controller focuses on review, not assembly. |
| Board pack preparation | Agent pulls actuals, variance tables, and commentary from accumulated data. Drafts standard sections for finance team review. | Board pack cycle compressed. Finance team focuses on narrative, not data gathering. |
| Internal audit trail | Every Clearledgr action is timestamped, attributed, and stored. Agent generates audit-ready reports on demand. | External audit preparation cost drops significantly. Evidence is already organised. |

### The Sequencing Principle

These workflows are not a roadmap to be executed in parallel. They are a trust ladder to be climbed in sequence. Each layer earns the data, the ERP access, and the organisational trust required to make the next layer work. A finance team that has run AP through Clearledgr for six months — with zero duplicate payments, measurable close compression, and a clean audit trail — will grant the agent permissions that a new customer never would.

> *The commercial implication is significant. Clearledgr enters at AP with a narrow, provable ROI. It expands across the finance function as trust accumulates. By the time it reaches treasury, payroll, and reporting, it is not competing with point solutions — it is the finance operating system. That transition happens without a new sale. It happens through demonstrated performance.*

### Payment Execution

The current architecture has Clearledgr scheduling payments through the ERP — NetSuite posts the payment instruction, the bank executes it. This is correct for V1. The expansion move is Clearledgr becoming the payment execution layer itself: not just telling the ERP to pay, but executing the transfer directly via bank integrations, Open Banking rails, or a licensed payment institution partner.

When Clearledgr owns payment execution, several things become possible that are not possible when payment runs through the ERP. Dynamic discounting — offering vendors early payment in exchange for a discount, calculated in real time from live cash flow data. Batch payment optimisation — grouping payments by bank, by currency, by settlement window to minimise fees and maximise float. FX management — converting and executing cross-border payments at optimal rates without a treasury team. Payment status tracking — real-time visibility of whether a payment has settled, bounced, or is pending, fed back into the invoice Box timeline automatically.

Payment execution requires regulatory consideration. In the UK, executing payments on behalf of a business requires either an e-money institution licence or a partnership with a licensed payment institution. Clearledgr's path is partnership first — integrate with a licensed rail (Stripe Treasury, Modulr, Railsr, or equivalent) and offer payment execution as a feature without acquiring a licence directly. Licensing becomes relevant when payment volume justifies the compliance overhead. This is not a V1 decision. It is a sequencing decision that belongs in the expansion roadmap.

### Gmail Power Features

Streak ships a suite of Gmail power tools alongside its CRM: Snooze, Send Later, Thread Splitter, Streak Share, file management within Boxes, call and meeting logging, and pipeline notifications. These are not CRM features — they are inbox productivity features that make Gmail more usable for the people who live in it. Clearledgr carries the equivalents of each, adapted for finance operations.

| Feature | Streak equivalent | Clearledgr implementation |
|---------|-------------------|---------------------------|
| File management in Boxes | Auto-extracts email attachments into the Box, accessible alongside emails, notes, and timeline. | Invoice PDFs, KYC documents, certificates of incorporation, bank statements, and GRN confirmations are attached to the relevant Box automatically. Accessible from the thread sidebar. Preserved as part of the audit trail for the statutory retention period. Auditors can access document sets from the Backoffice export, not by clicking through emails. |
| Snooze | Temporarily archives an email thread and returns it to the top of the inbox at a set time. | AP Managers can snooze a vendor email pending a procurement decision, a disputed invoice awaiting a credit note, or any thread that cannot be actioned today but must not be forgotten. Snoozed threads return with the Box context still active — the sidebar loads automatically on re-entry. The agent does not act on snoozed threads until they return. |
| Send Later | Schedule emails to send at a future date and time from within Gmail. | AP Managers composing direct vendor communications can schedule them to arrive at the start of the vendor's business day, after a weekend, or at a strategically chosen moment. The agent handles most vendor outreach — Send Later is for the personal communications that should not come from an automated system. |
| Thread Splitter | Breaks a Gmail thread where conversations have diverged into separate threads. | When a vendor email thread contains both an invoice and an unrelated query — common with long vendor relationships — the AP Manager can split the thread, creating a clean invoice thread for the agent to process and a separate thread for the query. Without splitting, the agent may process an ambiguous thread incorrectly. |
| Call and meeting logging | Log calls and meeting notes directly against a Box, with AI-generated meeting agendas from Box history. | When the AP Manager has a call with a vendor about a disputed invoice, an onboarding delay, or a payment query, they log it against the Box. The log includes date, duration, participants, and notes. The agent surfaces the log in the thread sidebar under the Box timeline so subsequent team members have full context. Call logs count toward the audit trail. |
| Pipeline notifications | Real-time Gmail desktop notifications when Boxes are updated, moved to new stages, or assigned. | Gmail-native notifications fire when the agent moves an invoice Box to a new stage, posts an exception, or activates a vendor. Finance team members in Gmail see these notifications immediately without checking Slack. The notification includes the Box name, the action, and a one-click link to the thread. Notification preferences are configurable per role in Settings. |
| Vendor enrichment | Auto-enriches Contacts with publicly available data from LinkedIn and social media. | When a new vendor is invited to onboard, the agent pre-populates their record from public registers: Companies House (UK legal name, registration number, registered address, director names), the HMRC VAT register (VAT number and registration status), and open corporate registries for non-UK vendors. This reduces the onboarding form burden on the vendor and accelerates KYC. Adverse media checks run automatically against the director names. Enrichment results are displayed in the Vendor record with the source and date of each data point. |
| Streak Share | Generate a shareable link to any Gmail thread and share it anywhere. | The AP Manager can generate a link to any invoice thread or vendor Box and share it in Slack, in an email, or in a document. The link opens the thread in Gmail with the Clearledgr sidebar already loaded — the recipient sees full context immediately. Access is permission-controlled: only team members with Clearledgr access can view the linked thread. |

### The Developer Platform

Streak exposes a REST API that powers most of its own functionality — creating Boxes, updating pipelines, querying contacts, firing webhooks on stage changes. Clearledgr's API follows the same model: the same actions available to the finance team in Gmail are available to developers and integration partners via API.

The API matters for three audiences. First, ERP partners and implementation firms who build custom connectors beyond the standard four (NetSuite, SAP, Xero, QuickBooks). Second, accounting firms who manage AP operations for multiple clients and want to build reporting dashboards, custom alerts, or white-labelled finance operations tools on top of Clearledgr's data. Third, enterprise customers with internal engineering teams who want to integrate Clearledgr events into their own data pipelines, monitoring systems, or compliance tooling.

| API capability | What it enables |
|----------------|-----------------|
| Invoice events | Webhooks fire on `invoice.received`, `invoice.matched`, `invoice.exception`, `invoice.approved`, `invoice.posted`, `invoice.paid`. External systems can react to these events in real time — updating dashboards, triggering downstream workflows, or feeding data lakes. |
| Vendor events | Webhooks fire on `vendor.invited`, `vendor.kyc_complete`, `vendor.bank_verified`, `vendor.activated`, `vendor.suspended`. Procurement systems, ERP master data tools, and compliance platforms can stay in sync with Clearledgr's vendor state without polling. |
| Pipeline queries | Read the current state of any pipeline — all Boxes, their stages, their Agent Column values, their timeline entries. Enables custom reporting, external dashboards, and audit exports without needing the Backoffice. |
| Agent action log | Query the full timeline of any Box or export all actions within a date range. The compliance backbone for customers who build their own audit tooling. |
| Settings API | Programmatically configure AP policy rules, approval routing, and autonomy thresholds. Enables large customers to manage Clearledgr configuration at scale without clicking through the Settings UI for each workspace. |

### The Vendor Record as a First-Class Object

Streak has Contacts and Organizations as persistent objects that exist independently of any single Box and link across multiple pipelines. A Contact can appear in the Sales pipeline, the Support pipeline, and the Hiring pipeline simultaneously, with a unified interaction timeline across all three.

For Clearledgr, the Vendor is the equivalent first-class object. A Vendor record exists independently of any individual invoice Box. It persists across every invoice from that vendor, linking all invoice Boxes to a single Vendor profile. The Vendor record holds: legal name, registration number, VAT number, registered address, director names, primary AP contact email, IBAN on file with verification status and date, KYC completion date, payment terms, YTD spend, invoice count, exception count, and the full onboarding history. When the agent opens the sidebar on any invoice email from Stripe Inc., it is reading from the Stripe Vendor record — not reconstructing data from previous emails. The Vendor record is the persistent financial relationship. The invoice Box is the transactional event within that relationship.

This distinction has practical consequences. Duplicate invoice detection works at the Vendor level — the agent checks all previous invoice Boxes for this Vendor, not just recent emails. Payment history is Vendor-level data — it drives the sidebar's historical payment record and informs the agent's confidence when a new invoice arrives. Vendor risk scoring accumulates at the Vendor level — a vendor with three recent exceptions is treated differently than a vendor with a clean 24-month history, and the agent's autonomy thresholds adjust accordingly.

### Multi-Entity Support

Mid-market and enterprise finance teams operate across multiple legal entities — subsidiaries, regional offices, holding companies. A single finance team often manages AP for three to eight entities simultaneously, each with its own ERP instance, chart of accounts, cost centre structure, and approval hierarchy. This is not an edge case. It is the default configuration for any company that has grown beyond a single trading entity.

Clearledgr's multi-entity architecture: each legal entity is a separate workspace within a parent account. The parent account holds the subscription and billing. Each workspace has its own ERP connection, its own AP Invoices pipeline, its own Vendor master, its own approval routing, and its own agent configuration. They operate independently by default — an invoice in Entity A does not appear in Entity B's pipeline.

Cross-entity visibility is the Controller and CFO's view. From the parent account, they can see a consolidated pipeline across all entities: total invoices in flight, total exceptions, total payments scheduled, aggregated by entity. They can drill into any individual entity's pipeline from the same Gmail interface. The consolidated view does not allow actions across entities — approvals happen at the entity level. It allows oversight across entities without requiring the CFO to switch between eight separate Gmail logins.

Cross-entity vendor management: a vendor who supplies multiple entities — a software vendor with company-wide contracts, a landlord with multiple property agreements — is managed as a single Vendor record at the parent account level, with entity-specific payment terms and IBANs configured per entity. KYC is completed once at the parent level. Each entity inherits the vendor's verified status. A vendor who fails KYC at the parent level is blocked across all entities simultaneously.

### Migration from Existing Tools

Every Clearledgr prospect is currently doing AP somewhere else. Bill.com customers have vendor records, invoice history, and payment data in Bill.com. NetSuite AP customers have their entire vendor master and invoice history in the ERP. Customers on spreadsheets have a tangle of data across Gmail, Google Sheets, and their accountant's inbox. Migration is not a one-time event — it is a trust problem. The finance team will not trust Clearledgr with their live AP workflow until they see it handle their actual vendors and their actual invoice formats correctly.

Clearledgr's migration approach is parallel running, not cutover. The customer continues their existing AP process while Clearledgr processes the same invoices in parallel for a minimum of two weeks. The AP Manager compares Clearledgr's match results against their existing process. Where they agree, confidence builds. Where they disagree, the team investigates — and usually finds that Clearledgr caught something the existing process missed. After two weeks, the customer makes an informed decision to cut over, with data supporting that decision rather than a sales promise.

| Migration source | What Clearledgr imports and how |
|------------------|----------------------------------|
| Bill.com | Vendor master export via Bill.com API, where available. Note: Bill.com API access for data export requires a partner relationship with Bill.com — it is not available to any developer on request. This is a business development dependency, not purely an engineering task. Until the partnership is established, Bill.com migrations use CSV export from the Bill.com interface. CSV export covers vendor name, email, and payment terms but may not include full IBAN data, which Bill.com does not expose in standard exports. In either case: bank details are verified again via micro-deposit after import. Clearledgr does not inherit Bill.com's verification as sufficient. |
| NetSuite AP | Vendor master is read directly from NetSuite during the ERP connection. No separate import required. Invoice history is backfilled from NetSuite's bill records for the duplicate detection window. The ERP connection is the migration. |
| Xero / QuickBooks | Same as NetSuite — vendor master and invoice history are read from the ERP connection. The self-serve onboarding flow handles this automatically for Starter customers. |
| Spreadsheet / manual process | Vendor master import via CSV upload. Template provided. The AP Manager uploads their vendor list, Clearledgr maps the columns, and the vendors are created in the system. Each vendor then goes through the standard bank verification flow — micro-deposit — before their first invoice is processed. Migration from manual takes longer but is structurally identical to standard onboarding. |
| Stampli / Tipalti | Vendor master export from the source system, imported via CSV. Invoice history is typically available via export — imported for duplicate detection backfill. Payment history is not directly transferable in most cases and is noted in the Vendor record as 'history prior to [date] available on request from previous system.' |

The migration guarantee: no live invoice is processed by Clearledgr without the AP Manager's explicit confirmation that the parallel running period is complete and they are satisfied with the results. Clearledgr does not cut the customer over automatically. The AP Manager clicks a button in Settings that reads 'Go live — Clearledgr will now process invoices as the primary AP workflow.' That action is logged, timestamped, and attributed. The cutover is a human decision, not an automated milestone.

### The 12-Month Commitment

Section 3's platform opportunity covers 22 workflows across six domains. The trust ladder framing is correct — each layer earns the right to the next. But 'earns the right' cannot be the only answer to sequencing questions. The team needs a committed 12-month sequence, separate from the aspirational platform vision.

The committed sequence for the 12 months following V1 launch:

| Quarter | What ships and why |
|---------|---------------------|
| Q1 — V1 Launch | AP Invoices pipeline with full 3-way match, Vendor Onboarding pipeline, Gmail label architecture, Slack/Teams integration, Google Workspace Add-on, self-serve onboarding for Xero/QuickBooks Starter customers, managed implementation for NetSuite/SAP Enterprise customers. |
| Q2 — Multi-entity and AR foundation | Multi-entity workspace structure for parent accounts. AR invoicing pipeline: the agent drafts and sends customer invoices from Gmail, tracks receipt, and chases overdue payments. Same infrastructure as AP, reversed direction. Targeting customers already on Clearledgr AP who are manually managing their AR in Gmail. |
| Q3 — Reconciliation and Google Sheets depth | Bank reconciliation: agent matches ERP transactions to bank statement entries, surfaces unmatched items. Expanded Google Sheets integration: scheduled exports of full pipeline data, agent action logs, and vendor master for Controller-level reporting. Outlook Add-on scoping begins — not shipped, requirement gathering only. |
| Q4 — Month-end close and payment execution pilot | Month-End Close checklist pipeline: the agent tracks close tasks, surfaces blockers, and notifies the Controller when the close is at risk. Payment execution pilot with a licensed payment institution partner for 3-5 Enterprise customers — direct bank integration, not ERP-intermediated. Learnings inform whether payment execution becomes a general feature or remains a specialist offering. |

Everything beyond Q4 — flux analysis, FP&A, treasury, procurement, payroll — is aspirational and sequenced by customer demand. The trust ladder applies: no expansion workflow ships before the previous layer has demonstrable adoption and satisfaction in the customer base. The Q4 commitment is the ceiling of what the team plans for. The platform vision is the ceiling of what the product can become.

---

## 4. Design Principles

Every product decision — surface, interaction pattern, agent behaviour, information hierarchy — is governed by these principles. They are not aspirational. They are constraints. When a design decision conflicts with a principle, the principle wins.

### 01 — Everything lives inside Gmail

Clearledgr is a Gmail extension built on InboxSDK — the same SDK Streak uses to build their own product. There is no separate web application in V1. There is no new tab. There is no dashboard the AP team checks. The product injects into Gmail's left navigation, Gmail's inbox rows, Gmail's email thread view, and Gmail's compose window. Finance teams open their email in the morning and Clearledgr is already there.

### 02 — The agent acts. The human decides on exceptions.

The agent handles everything it can handle with high confidence: parsing invoices, running 3-way matches, routing approvals, chasing vendors, posting to the ERP. It escalates only what requires human judgment. The ratio of automated to manual actions is the primary product metric. A finance team using Clearledgr at full adoption should directly touch fewer than one in ten invoices. The interface exists for the exceptions, not the happy path.

### 03 — Every autonomous action is explainable

The agent never acts silently. Every action posts a timestamped entry to the Box timeline with three pieces of information: what it did, why it did it, and what happens next. This is the mechanism by which trust accumulates. Finance teams will not grant autonomous action to a system they cannot audit. Explainability is the prerequisite for expanded permissions, not a feature added on top.

### 04 — Exceptions are the only interruptions

The product is silent on the happy path. A 3-way match that passes within tolerance generates no notification. A payment posted to NetSuite per agreed terms generates no notification. Alerts, stage label changes, and Slack messages exist only for genuine exceptions. Signal-to-noise ratio is a design KPI measured as deliberately as any financial metric.

### 05 — The ERP is the system of record. We are the interface.

Clearledgr reads from and writes to the customer's ERP — NetSuite, SAP, Xero. It does not store financial data. It does not become a system of record. This is both a technical architecture decision and a trust decision. Enterprise finance teams will not migrate their data to a new platform. Positioning as the intelligent interface to an existing system of record removes the single biggest objection in the enterprise sale.

### 06 — Design for the Controller and CFO, not only the AP clerk

The AP clerk uses the product every day and drives adoption. The Controller and CFO make the buying decision and the renewal decision. Every pipeline view, every metric, and every agent action must be legible to a CFO in under two minutes without explanation. Density is acceptable. Ambiguity is not.

### 07 — Performance is a design constraint, not an engineering afterthought

A Gmail extension that slows the inbox gets uninstalled. A sidebar that takes five seconds to load gets ignored. A pipeline Kanban that freezes on scroll gets abandoned. Every Clearledgr injection point has a performance cost that must be budgeted before a single line of UI is designed. The target: inbox labels render before the user finishes reading the subject line. The sidebar loads within two seconds of thread open. The Kanban first paint completes in under one second. Clearledgr Home is fully interactive before the AP Manager has reached for their mouse. These are not aspirations — they are pass/fail criteria for each surface. A surface that fails its performance criterion does not ship.

### 08 — The product must survive Gmail changing — and the agent making mistakes

Two categories of failure can end customer trust without warning. First: Gmail changes its DOM. Clearledgr must be built to handle this without a Chrome Web Store submission, without user reinstallation, and without a support spike. InboxSDK's MV3-compatible NPM package is the foundation. The engineering team monitors InboxSDK releases as a primary operational responsibility. Every release is tested against all seven injection points before deployment. A DOM change that breaks a single surface is a P0 incident. Second: the agent extracts or matches incorrectly. Deterministic guardrails constrain the LLM's judgment before any ERP write executes (§7.6). Shadow mode and historical replay testing validate model changes before they reach customers (§7.7). Disaster recovery procedures define the response when something gets through anyway (§7.8). Both failure modes are designed for, not hoped against.

### 09 — The product must be invisible to the AP Clerk

Streak's simplicity is visual — a spreadsheet, labels, a sidebar. Clearledgr's simplicity is operational. The product's complexity is inside the agent and inside the Implementation Service. The daily experience of the AP Clerk should have none of it. An AP Clerk using Clearledgr should experience exactly one change to their workflow: invoices that arrive in the inbox have a coloured stage label next to the subject line. That is the entire interface for the happy path. The sidebar, the pipeline Kanban, the agent feed, the automation rules — these exist for exceptions, for oversight, and for configuration. They are not part of the AP Clerk's day unless something goes wrong. If an AP Clerk who has used Clearledgr for a month describes their experience as 'I just see the labels and everything gets handled', the product is working. If they say 'I check the pipeline every morning and review each match result', the product has failed to earn enough trust to act autonomously. The AP Clerk's experience is the benchmark. Everything else — the CFO's digest, the Controller's Kanban, the AP Manager's Settings — is built around that core of invisibility.

---

## 5. The Object Model

Clearledgr uses Streak's object model directly. Understanding this model is the prerequisite for understanding how every surface in the product works.

### 5.1 Core Objects

| Object | What it is in Clearledgr |
|--------|--------------------------|
| Box | One invoice record or one vendor onboarding record. Each Box stores the email thread that created it, all agent actions on its timeline, all extracted field values, and any linked Boxes (e.g. an invoice Box linked to its vendor Box). |
| Pipeline | A collection of Boxes moving through a defined workflow. Clearledgr has two pipelines in V1: AP Invoices and Vendor Onboarding. Each is accessible from Gmail's left nav and has its own Kanban view. |
| Stage | The current step of a Box in its workflow. For AP: Received, Matching, Exception, Approved, Paid. For Vendor Onboarding: Invited, KYC, Bank Verify, Active. The agent moves Boxes between stages autonomously based on its actions. |
| Column | A data field on every Box in a pipeline. Clearledgr's agent columns are auto-populated — no manual entry required. Examples: Invoice Amount, PO Reference, Match Status, Exception Reason, Days to Due Date, IBAN Verified. |
| Timeline | The chronological record of everything that happened to a Box: all emails in the thread, every agent action with its reasoning, all comments, all field updates. The timeline is the audit trail. |
| Saved View | A filtered slice of a pipeline surfaced directly in the Gmail inbox. Example: a Saved View called 'Exceptions' shows all invoices with a failed match as a labelled section at the top of the AP team's inbox every morning. |

### 5.2 The Shared Inbox Model

Streak automatically shares all emails added to a Box with every team member who has pipeline access. No CC required. Everyone sees the full communication history with any contact. Clearledgr applies this directly to finance operations.

When the agent creates an invoice Box from an incoming vendor email, that email is automatically shared with all members of the AP team. The AP Clerk, AP Manager, and Controller all see the vendor invoice without being CC'd on the original. The AP team's shared ap@ inbox becomes a team workspace: every email arriving at that address is visible to everyone with AP Invoices pipeline access, giving the entire team situational awareness without a shared mailbox product.

The practical effect: when an AP Manager is out, a colleague can cover with full context on every in-flight invoice, every vendor conversation, and every agent action. The Box timeline is the handover document. Nothing is lost between people.

### 5.3 @Mentions — The In-Gmail Escalation Path

Streak allows team members to @mention colleagues in Box timeline comments, triggering Gmail notifications. Clearledgr uses this as the first-line escalation path — keeping work inside Gmail before it escalates to Slack or Teams.

When the agent flags an exception it cannot resolve autonomously, it posts to the Box timeline with a specific @mention: '@Sarah — INV-2840 from AWS EMEA has a delta of £422 vs GRN receipt. Within your authority to override. Match detail in sidebar.' Sarah receives a Gmail notification, opens the thread, and the sidebar is already showing the discrepancy. One click to override or reject — without leaving Gmail.

The @mention bridge: when a Box timeline @mention happens in Gmail, Clearledgr also sends a Slack or Teams DM to the mentioned person with the comment and a direct link to the thread. Their reply from Slack posts back to the Box timeline. Gmail and Slack stay in sync without requiring the user to check both platforms.

The distinction matters and must be enforced by design: @mentions in Box timelines demand a response from a specific person about a specific invoice. Slack channel messages are informational. Finance teams learn to treat them accordingly — and the product must make the distinction visible.

### 5.4 Archived Users

Streak preserves data from deactivated users without charging for their seat. This is not a minor billing convenience — for Clearledgr it is a compliance requirement. When an AP team member leaves a company, their Box timeline contributions, agent action attributions, and approval records are financial records. They cannot be deleted. They must remain visible, attributed to the person who took the action, for the duration of the retention period.

When a team member is removed from Clearledgr, their access is revoked immediately. Their seat billing stops at the end of the current billing period. Their name, email, and all timeline contributions remain intact and visible across every Box they touched. Any approval they granted, any override they made, any exception they resolved — all permanently preserved in the audit trail. The person is gone from the product. Their record remains.

### 5.5 Agent Columns

In Streak, Magic Columns are fields auto-populated by system activity — last email date, days in stage, email count. Clearledgr's equivalent is Agent Columns: fields populated by the agent from its autonomous processing. No member of the finance team ever types into these fields.

| Agent column | How it is populated |
|--------------|---------------------|
| Invoice Amount | Extracted by the agent from the email attachment — PDF, XML, or structured email body. |
| PO Reference | Extracted from the invoice and validated against open POs in the ERP. |
| GRN Reference | Matched from the ERP goods receipt records to the relevant PO line items. |
| Match Status | Set by the agent after 3-way match: Passed, Exception, or Failed, with tolerance delta. |
| Exception Reason | Written by the agent in plain language: 'Invoice €8,922 vs GRN receipt €8,500 — delta €422 exceeds 2% tolerance.' |
| Days to Due Date | Calculated from the invoice due date. Drives payment scheduling and urgency sorting. |
| IBAN Verified | Set to Verified or Unverified after micro-deposit confirmation during vendor onboarding. |
| ERP Posted | Timestamp of when the agent posted the invoice to the ERP after approval. |

---

## 6. The Surfaces

Clearledgr V1 has three surfaces: Gmail, Slack or Microsoft Teams, and the Google Workspace Add-on for mobile. These surfaces have distinct and non-overlapping roles. Gmail is the work surface — where invoices are processed, where the agent acts, where the pipeline lives, where the audit trail accumulates. Slack and Teams are the decision surface — where approvals are granted, where escalations are resolved, where trust signals are delivered. The Google Workspace Add-on is the mobile decision surface — lightweight approval access from the native Gmail app when the approver is away from their desk.

This split is deliberate, not a compromise of the Gmail-first thesis. The thesis is that finance work happens in email and the product should live there. Approval decisions are not work — they are judgments. Routing judgments to Slack is correct because that is where senior stakeholders already make decisions during their day. Routing the work itself to Slack would be the mistake. Every invoice thread, every vendor record, every Box timeline, every agent action — these live in Gmail. The approval of an outcome lives in Slack. The distinction holds.

Within Gmail, Clearledgr injects into seven specific locations using InboxSDK. Each injection maps to a distinct InboxSDK API and serves a distinct purpose. Understanding these seven injection points is the complete understanding of what the product is inside Gmail.

### 6.1 Clearledgr Home — The Daily Starting Point

Streak Home is a centralised hub in Gmail's left nav showing recent boxes, upcoming tasks, quick access to all pipelines, trending activity, and product updates. It is the first thing a Streak user sees when they arrive in Gmail. Clearledgr has the equivalent: Clearledgr Home.

Clearledgr Home is the first entry in the Clearledgr left nav section. It opens as a custom route view inside Gmail — the same mechanism as the pipeline Kanbans. It is the daily briefing surface: everything the finance team needs to know today, and nothing they do not.

| Home section | What it shows |
|--------------|---------------|
| Exception Queue | All invoices currently in Exception or Failed status, ordered by due date. This is the most urgent section — it sits at the top because exceptions that are not resolved before due date become payment failures. Each exception shows the vendor, amount, and the specific reason in one line. |
| Awaiting Your Approval | Invoices the agent has matched and routed to this user for sign-off. Ordered by due date. Approve or reject in one click from this view — no need to open the email thread. |
| Due For Payment This Week | Matched and approved invoices scheduled for payment in the next 7 days. Gives the AP Manager a cash flow view without a treasury tool. |
| Agent Actions Today | A condensed feed of what the agent has done since midnight. Not the full Agent Activity view — just the last 10 actions, each in one line. Gives the team situational awareness at a glance. |
| Vendor Onboarding Blockers | Any vendor onboarding engagement that is currently blocked — a missing document, an unconfirmed micro-deposit, a vendor who has not responded in 48 hours. Directly actionable from this view. |
| Quick Access | One-click buttons to AP Invoices pipeline, Vendor Onboarding pipeline, and Agent Activity. Also shows the Clearledgr notification badge count across all categories. |

Clearledgr Home is designed for the first 90 seconds of the AP Manager's day. It answers: what broke overnight, what do I need to approve, what is due soon, and what is the agent working on right now. If those four questions are answered in under two minutes, the AP Manager can trust the product enough to let it run without constant checking.

### 6.2 The Left Navigation — NavMenu

InboxSDK provides `sdk.NavMenu.addNavItem()` to add entries to Gmail's left sidebar. Clearledgr adds a dedicated section to Gmail's left nav, at the same level as Inbox, Starred, and Sent. The section contains two pipelines and one feed, plus Saved Views nested beneath.

| Nav entry | What it opens |
|-----------|---------------|
| AP Invoices | The AP pipeline Kanban view — all invoice Boxes organised by stage. Badge shows exception count. |
| Vendor Onboarding | The vendor onboarding Kanban — all vendor Boxes from Invited to Active. |
| Agent Activity | The complete feed of all autonomous actions across both pipelines, newest first. |
| Exceptions (Saved View) | Filters AP Invoices to show only Boxes with Match Status = Exception or Failed. |
| Awaiting Approval (Saved View) | Filters AP Invoices to show Boxes routed for human approval but not yet actioned. |
| Due This Week (Saved View) | Filters AP Invoices by Days to Due Date ≤ 5. |

Saved Views can be set to 'Show in Inbox' — this surfaces the filtered Box list as a labelled section at the top of the Gmail inbox. An AP team arriving in the morning sees their Exceptions queue at the top of their inbox without clicking anywhere.

### 6.3 Inbox Stage Labels — Lists

InboxSDK provides `sdk.Lists` to modify how email threads appear in the inbox row. Clearledgr injects a coloured stage label inline on every inbox row that belongs to a Box. The label sits between the sender name and the email subject.

A finance team scanning their inbox sees at a glance: which invoices are matched and ready to approve, which have exceptions, which are scheduled, which are awaiting a missing PO. They do not open the email to understand its status. The label communicates it.

Label colours carry consistent meaning across the entire product: green for passed or active, amber for exception or pending human action, red for failed or blocked, blue for approved and in progress, grey for neutral or not yet processed.

### 6.4 Email Triage and the Gmail Label Architecture

Before the agent can process an invoice, it must classify every incoming email in the AP inbox. This is the triage layer — the work that makes everything else function correctly. Streak uses Email Filters to match incoming emails to existing Boxes based on sender address or domain. Clearledgr's triage layer is more active: the agent reads every incoming email, classifies it, and decides what to do with it before any other surface is involved.

Clearledgr uses two labelling mechanisms simultaneously. The first is InboxSDK's Lists injection — the extension renders stage badges inline on inbox rows. Visible only when the extension is active, rich in display: vendor name, invoice amount, stage colour, monospace reference. The second is Gmail native labels applied via the Gmail API — real labels written into Gmail's own data model, visible everywhere Gmail runs regardless of whether the extension is installed. Mobile, desktop, any email client, Google Admin Console. The native label is the foundation. The InboxSDK injection is the upgrade on top. Together they ensure every classified email is labelled persistently and displayed richly.

#### The Gmail Label Hierarchy

On first installation, Clearledgr creates a nested label hierarchy in the customer's Gmail workspace using the Gmail API. These labels are owned by the customer's Google account — not by Clearledgr. If Clearledgr is ever removed, the labels remain. The AP team can modify label names and colours from within Gmail's native settings, within the constraints of the Clearledgr hierarchy.

| Gmail label | What it means — applied by the agent |
|-------------|---------------------------------------|
| `Clearledgr/Invoice/Received` | Email classified as an invoice. Box created. Extraction in progress. Not yet matched. |
| `Clearledgr/Invoice/Matched` | 3-way match passed. Awaiting approval. The AP Manager's action queue. |
| `Clearledgr/Invoice/Exception` | Match failed or flagged. Requires human resolution. Highest urgency label — rendered in amber by default. |
| `Clearledgr/Invoice/Approved` | Approved by the AP Manager or auto-approved within threshold. Payment scheduled. |
| `Clearledgr/Invoice/Paid` | Payment executed and confirmed by ERP. Thread archived after 30 days by default. |
| `Clearledgr/Vendor/Onboarding` | Email related to an active vendor onboarding engagement. Routed to Vendor Onboarding pipeline. |
| `Clearledgr/Finance/Credit Note` | Classified as a credit note. Logged against the relevant invoice Box. AP Manager notified. |
| `Clearledgr/Finance/Statement` | Vendor statement of account. Logged against the Vendor record. No Box created. |
| `Clearledgr/Finance/Query` | Vendor payment query or invoice dispute. Routed to AP Manager. Agent attempts to respond if it holds the answer. |
| `Clearledgr/Finance/Renewal` | Contract renewal or price increase notice. Routed to procurement contact configured in Settings. |
| `Clearledgr/Review Required` | Agent could not classify with sufficient confidence. Surfaced to AP Manager for manual classification. Reviewed in Clearledgr Home. |
| `Clearledgr/Not Finance` | Promotional, newsletter, or system notification from a finance-adjacent sender. Not actioned. Archived after 7 days. |

#### The Classification Logic

Every email arriving in the AP inbox — whether sent to the shared ap@ address or to an individual team member's address monitored by Clearledgr — passes through the agent's classification layer before anything else. Classification happens within 30 seconds of receipt.

| Classification decision | Agent behaviour |
|-------------------------|-----------------|
| Invoice identified — known vendor | Matches the sender to an existing Active vendor in the master. Creates an invoice Box. Applies Clearledgr/Invoice/Received label. Begins 3-way match. Email shared with AP team via automatic email sharing. |
| Invoice identified — unknown vendor | Sender not in vendor master. Applies Clearledgr/Review Required label. Posts to AP Manager: 'Invoice received from [sender] — not in vendor master. Initiate onboarding or reject.' No Box created until vendor is activated. |
| Invoice identified — duplicate | Invoice amount and vendor match an existing Box within the duplicate detection window. Applies Clearledgr/Review Required label. Posts to AP Manager: 'Possible duplicate of INV-2801 paid on 14 March. Confirm this is a new invoice before processing.' Original Box linked in the notification. |
| Invoice in existing thread | New invoice attached to a reply in an old email thread. Agent detects the new attachment, creates a new Box, and — where thread history makes it ambiguous — offers the AP Manager a Thread Split to separate the new invoice from the historical thread. |
| Credit note | Applies Clearledgr/Finance/Credit Note label. Logs against the original invoice Box if identifiable from the reference number. Updates the outstanding amount on the Vendor record. Notifies AP Manager. |
| Payment query from vendor | Applies Clearledgr/Finance/Query label. Agent checks ERP for payment status of the referenced invoice and, if confirmed paid, drafts a response with remittance details for AP Manager approval. If not paid, flags for AP Manager review. |
| Vendor statement | Applies Clearledgr/Finance/Statement label. Logs against the Vendor record. Agent reconciles the statement against its own invoice records and flags any discrepancies for AP Manager attention. |
| Onboarding-related | Detects an email from a vendor in an active onboarding engagement. Routes to the Vendor Onboarding Box for that vendor. Updates the onboarding checklist if the email contains a submitted document. |
| Unclassifiable | Agent confidence below threshold. Applies Clearledgr/Review Required label. Surfaces in Clearledgr Home under a dedicated 'Needs classification' section. AP Manager classifies manually with one click — their classification trains the agent for future similar emails. |
| Clearly irrelevant | Promotional, newsletter, or automated system email. Applies Clearledgr/Not Finance label. No action taken. Archived after the configured period. False positives here are reported by the AP team from the thread toolbar. |

#### Label Modification — The Finance Team's Control

The finance team can modify Clearledgr's Gmail labels from two places: Gmail's native label settings for renaming and recolouring, and Clearledgr's Settings > Labels for controlling behaviour — which labels trigger notifications, which archive automatically, and which surface in Clearledgr Home.

The AP Manager can customise label colours to match their company's conventions. They can rename labels within the Clearledgr hierarchy — 'Clearledgr/Invoice/Exception' might become 'Clearledgr/Invoice/Needs Review' for a team that finds the word exception alarming. They cannot delete stage labels or collapse the hierarchy — the label structure is tied to the pipeline stages and removing it would break the triage logic. The constraints are surfaced clearly in Settings with an explanation of why they exist.

For Google Workspace enterprise customers, the label hierarchy is visible to IT administrators in Google Admin Console. This matters for compliance — it gives IT a view of how finance emails are being categorised without requiring them to access Clearledgr itself. It also means the labels survive any Clearledgr outage — classified emails remain labelled even if the extension is temporarily unavailable.

#### Monitoring Individual Inboxes

The shared ap@ inbox is the primary monitoring surface. But AP team members also receive finance emails directly — a vendor who has an existing relationship with a specific person at the company, a CFO who receives a payment query, a Controller CC'd on an invoice. Clearledgr monitors individual team member inboxes for finance-relevant emails, with each team member's explicit consent configured during onboarding.

When the agent detects a finance email in an individual's inbox, it applies the appropriate Clearledgr label, creates or updates the relevant Box, and posts a note to the shared pipeline: 'Invoice from Stripe Inc. received in Sarah's inbox — added to AP Invoices pipeline.' The email is not moved or forwarded. The individual retains it in their inbox. The pipeline gains visibility of it. Both the individual and the team have full context.

### 6.5 The Thread Toolbar — Toolbars

InboxSDK provides `sdk.Toolbars.registerThreadButton()` to add buttons to the email toolbar when a thread is open. Clearledgr adds three buttons to the toolbar of any email thread that belongs to an AP Box.

| Button | Action |
|--------|--------|
| Approve | Shown when Match Status = Passed. Triggers agent to post to ERP and schedule payment. One click. No navigation required. |
| Review Exception | Shown when Match Status = Exception. Opens the Thread Sidebar to the match detail with the exception highlighted. The human sees exactly what the agent found and decides whether to override or reject. |
| NetSuite ↗ | Opens the relevant invoice record directly in the ERP. Available on all AP thread views. |

These toolbar buttons are the primary action surface for AP Managers. An AP Manager who receives a Slack notification about a pending approval, opens the email, sees the match status in the stage label, and approves in one click from the toolbar — without opening any other system.

### 6.6 The Thread Sidebar — Conversations

InboxSDK provides `sdk.Conversations.registerThreadSidebarContentPanelHandler()` to inject a panel into the right sidebar whenever an email thread is opened. This is the primary context surface. It activates automatically when any email belonging to a Box is opened — no click required.

The sidebar is divided into four sections, always in this order:

| Sidebar section | Contents |
|-----------------|----------|
| Invoice | Amount due (large, monospace), invoice reference, PO reference, due date, payment terms. Agent columns displayed as read-only fields. The factual record of the invoice. |
| 3-Way Match | Three rows: Purchase Order, Goods Receipt, Invoice. Each shows matched reference, status icon (✓ / ⚠ / ✗), and the specific value if there is a discrepancy. This is the core AP decision interface. |
| Vendor | Vendor name, category, YTD spend, invoice count, exception count, payment terms, IBAN on file with verification status. Historical payment record beneath. |
| Agent Actions | The Box timeline filtered to this invoice. Each entry shows: what the agent did, why it did it, and what happens next. This is the explainability record for this specific invoice. |

Below the four sections: an Approve button (shown when match passed), and a natural language query field. The query field lets the finance team ask the agent questions about the vendor or invoice without navigating away. 'What's our outstanding with Stripe?' or 'Why was this flagged?' — answered in thread.

The sidebar loads within two seconds of opening a thread. If it loads slowly, finance teams close it and revert to manual. Load time is a design specification, not an engineering afterthought.

This constraint applies to every Clearledgr injection point, not only the sidebar. The inbox must not slow down as Gmail loads stage labels across hundreds of email rows. The Kanban pipeline view must render its first paint before the finance team has finished reading the page title. The Clearledgr Home must load faster than the AP Manager can reach for their coffee. Performance is not a feature. It is the prerequisite for everything else in this document working as described.

### 6.7 The Pipeline Kanban Views — Router

InboxSDK provides `sdk.Router.handleCustomRoute()` to register custom page views within Gmail's own chrome. Clicking 'AP Invoices' or 'Vendor Onboarding' in the left nav does not open a new tab. It replaces the inbox content area with a Kanban board, rendered inside Gmail's existing window.

The pipeline views use Kanban, not spreadsheet. Streak uses a spreadsheet format because they built a general-purpose CRM requiring column-level flexibility. Clearledgr's pipelines are fixed workflows with defined stages. Kanban is the right form for a fixed workflow: it communicates volume, distribution, and blockage at a glance without requiring column configuration.

Each Kanban column represents a stage. Cards are Boxes — one per invoice or vendor. Cards show the vendor name, invoice amount, reference number, and age. Exception cards surface the specific flag inline. Clicking a card opens the email thread, which activates the Thread Sidebar automatically.

### 6.8 Slack and Microsoft Teams — The Communication Layer

Streak does not have a Slack integration designed for finance operations. Clearledgr does — and the design of this surface is where Clearledgr departs from the Streak reference most significantly. Slack and Teams are not bolt-ons. They are a fully designed communication layer that extends the Gmail-native product to wherever the finance team is: in a meeting, on a call, away from their desk.

The finance team chooses their platform — Slack or Microsoft Teams. Clearledgr connects to both. The experience is identical. Everything described below applies to both platforms. The word Slack is used for brevity.

#### The Design Philosophy for Slack

Most product Slack integrations are notification systems: something happens, a message appears. Clearledgr's Slack integration is a workflow surface. The agent does not just notify — it presents decisions in a format that requires no context-switching to act on. An AP Manager who approves an invoice from Slack never needed to know Gmail was involved. The work moved through Gmail. The decision happened in Slack. The ERP was updated. Nobody left their communication tool.

#### Interactive Approval Messages

When the agent routes an invoice for approval, it sends a structured interactive message — not a text notification. The message contains everything the approver needs to decide:

| Message element | Content and purpose |
|-----------------|---------------------|
| Header | Vendor name, invoice reference, and amount in large type. Scannable in under two seconds. The AP Manager knows what they are looking at before reading another word. |
| Match result | Three icons in a row: Purchase Order ✓, Goods Receipt ✓, Invoice ✓. Or with discrepancy: GRN ⚠ — £422 delta. Visual at a glance. No text required to understand the status. |
| Agent reasoning (threaded) | Posted as a reply thread below the main message: 'Match passed within 0.3% tolerance. PO-2041 confirmed. GRN-1892 confirmed. Vendor IBAN verified. Payment terms Net 30 — due 14 April.' The AP Manager can expand the thread if they want the full reasoning. It is not in the main message. |
| Actions | Two primary buttons: Approve and Reject. A third: Request info — which opens a modal where the AP Manager can type a question to the vendor, dispatched as an email from the AP inbox. All actions execute without leaving Slack. |
| Confirmation | On approval: the message updates immediately. '✓ Approved by Sarah Chen, 09:41. Posted to NetSuite. SEPA scheduled 14 April. Override available until 09:56.' The agent acts and confirms in the same message. There is no second notification. |

#### Exception Messages — Designed for Resolution

Exception notifications are not alerts. They are decision packages. When the agent cannot proceed, it sends a message that includes the exception, the context, and the available resolution paths — all in one place.

| Exception element | Content and purpose |
|-------------------|---------------------|
| Exception statement | Specific and immediate: 'INV-2840 from AWS EMEA — invoice £8,922 vs GRN receipt £8,500. Delta £422. Exceeds 2% tolerance.' Not 'amount mismatch detected.' The first line tells the AP Manager everything they need to know. |
| Resolution options | Three buttons presented inline: Override and approve (with a mandatory reason field), Request credit note from vendor (agent sends the email), Reject invoice (agent notifies vendor and closes the box). The AP Manager selects one. The agent executes. |
| Context thread | The agent threads the full match detail below the main message for AP Managers who want to dig deeper before deciding. Not required reading — optional depth. |
| Timer | If the invoice due date is within 48 hours, a countdown appears: 'Payment due in 31 hours. Override required before 17:00 today to avoid late payment.' Urgency is surfaced without being manufactured — it reflects actual payment terms. |

#### The Conditional Digest — Structured Briefing Only When It Matters

Design Principle 4 states that exceptions are the only interruptions. A digest posted every morning regardless of content is an interruption — it trains the finance team to ignore it. The digest fires only when it has information the AP team needs to act on. On any working day where the previous 24 hours produced no exceptions, no pending approvals, and no onboarding blockers, no digest is sent. Silence is the signal that everything ran correctly. A team that receives a digest only when it matters will read every word. A team that receives one every morning regardless will stop reading within a week.

| Digest section | Content |
|----------------|---------|
| What the agent handled | Count and summary of invoices processed overnight without human involvement. '5 invoices processed automatically — £47,200 total. No exceptions.' If the number is zero or there were exceptions, this section adapts accordingly. |
| What needs you today | Exceptions and pending approvals, ordered by urgency. Each line: vendor name, amount, what is needed, due date. Maximum 5 items shown — if more exist, a 'See all 8' button opens the Exceptions saved view in Gmail. |
| Due for payment this week | Approved invoices scheduled in the next 5 working days. Vendor, amount, payment date. Cash flow visibility without a treasury tool. |
| Vendor onboarding status | Any vendor stuck in onboarding for more than 48 hours. Vendor name, stage, what is missing, days elapsed. One line each. |
| Agent confidence note | If the agent's match accuracy for the previous week is below the customer's established baseline, it flags this: 'Match accuracy was 94.1% this week vs 97.3% baseline. Two cases reviewed.' Transparency about performance, not just outcomes. |

#### Vendor Onboarding Chase Notices

Before the agent sends a chase email to a vendor who has not responded to an onboarding step, it posts a preview to the AP channel. This is intentional — the finance team may have context the agent does not, such as knowing the vendor's contact is on leave.

The message reads: 'About to chase Paystack (ops@paystack.com) for their certificate of incorporation — 48h since first request. Sending in 30 minutes unless you hold it.' Two buttons: [Hold chase] and [Send now]. If no response within 30 minutes, the agent sends automatically. If held, it asks for a reason and logs it to the Box timeline.

This is the agent operating with awareness that it does not know everything. It acts within its authority but gives the team a window to intervene when they have context the agent lacks.

#### Conversational Queries in Slack

The AP team can ask the agent questions directly in a dedicated Clearledgr Slack channel or by direct message. The agent reads the channel and responds in thread. No slash commands, no structured syntax — plain English questions answered with live ERP data.

| Example query | What the agent returns |
|---------------|------------------------|
| "What's our outstanding with AWS this month?" | 'AWS EMEA has 2 open invoices this month: INV-2840 (£8,922 — exception, awaiting your review) and INV-2843 (£4,200 — matched, due 18 April). Total outstanding: £13,122.' |
| "Which invoices are due this Friday?" | '3 invoices due Friday 11 April: Deel HR BV £31,200 (approved, SEPA scheduled), Notion Labs £1,450 (pending your approval), Linear App £890 (matched, ready to approve).' |
| "Has Brex finished their onboarding?" | 'Brex Inc. is at KYC stage — their certificate of incorporation has not been received. The agent chased them yesterday at 09:12. No response yet. Want me to escalate to their finance director?' |
| "Show me everything the agent did between 6pm and 9am" | A condensed timeline of all autonomous actions in that window. Each line: timestamp, action, invoice or vendor, outcome. |

#### The Override Window Notification

When the agent takes an autonomous action — posting to the ERP, scheduling a payment — it sends a brief Slack notification to the AP Manager with a time-limited undo option. '✓ Posted INV-2841 to NetSuite. Payment of £12,400 to Stripe Inc. scheduled 14 April. Override window closes in 15 minutes: [Undo]' The button is live for the override window, then the message updates to 'Override window closed. Payment confirmed.' The AP Manager always has a human-accessible escape hatch on autonomous actions, and they always know exactly when it closes.

#### Intelligent Routing

The agent does not send all messages to a single channel. Routing is configured in Settings > Approval Routing and reflects the customer's actual approval hierarchy.

| Routing rule | How it works |
|--------------|--------------|
| Standard approval | Routed to AP Manager's DM — not the channel. Approval decisions are personal, not team spectator events. The channel gets the digest, not every approval request. |
| Above AP Manager threshold | Routed to Controller's DM, with a copy to the AP channel for visibility. The Controller decides. The AP team sees it happened. |
| CFO-level sign-off | Routed directly to CFO DM. Includes a 4-hour response window. If no response, escalates to the Controller with a note that the CFO was unavailable. The agent never lets a payment stall silently. |
| Exception requiring procurement | Routed to the procurement contact configured in Settings, not the AP team. A no-PO invoice is a procurement problem, not an AP problem. The agent knows the difference. |
| Out-of-office routing | If the assigned approver's Google Calendar shows OOO, the agent routes to their backup. Backup is configured per role in Settings. The agent never waits for an approver who is not available. |

> *The test for every Slack message the agent sends: does the recipient have everything they need to act without opening another tool? If the answer is no — if they need to click a link, open Gmail, look something up — the message has failed. Every Clearledgr Slack message should be self-contained.*

#### Google Sheets Export — The Reporting Escape Valve

Finance teams live in spreadsheets. Clearledgr's analytics dashboard covers the standard views. But controllers and finance directors often need custom cuts of data that no pre-built dashboard anticipates: spend by cost centre for the board report, exception rate by vendor category for the CFO's operational review, payment timing analysis for cash flow modelling.

Clearledgr integrates directly with Google Sheets. From Settings > Integrations > Google Sheets, the AP Manager can configure exports of any pipeline data — invoice volume, agent action logs, match accuracy, vendor onboarding duration — to a nominated spreadsheet, on a configurable schedule. Daily, weekly, or on-demand. The sheet refreshes automatically. No manual export, no CSV download, no email attachment.

This is available on Professional and Enterprise plans. It is the bridge between Clearledgr's structured data and the unstructured analysis that finance teams will always do in spreadsheets. The product does not try to replace that. It feeds it cleanly.

### 6.9 The Google Workspace Add-on — Mobile Approvals

The Chrome extension is the core product. But enterprise finance teams have CFOs and Controllers who need to approve invoices from their phones — in meetings, travelling, between calls. Requiring them to switch to a laptop for a one-click approval is friction that breaks the product's promise.

Clearledgr publishes a Google Workspace Add-on in the Google Workspace Marketplace. This is separate from the Chrome extension and separate from any standalone mobile app. It works inside the native Gmail app on iOS and Android. When an email thread belongs to a Clearledgr Box, the Add-on surfaces a lightweight panel showing: invoice amount, match status, and an Approve or Reject button. Nothing else. The panel loads fast, works on a phone, and requires no additional authentication beyond Google.

The Add-on is for approvals only. It is not a full Clearledgr experience on mobile. An AP Clerk processing invoices needs the full Chrome extension on a desktop. A CFO approving a £200,000 payment from their phone needs the Add-on. Both needs are real. The product serves both without conflating them.

### 6.10 The Google Workspace Marketplace — Enterprise Distribution

Streak is listed in the Google Workspace Marketplace. This matters for enterprise customers. IT administrators at enterprise companies do not install browser extensions one user at a time — they approve and deploy through the Marketplace. A Clearledgr listing in the Marketplace is not a marketing decision. It is a procurement requirement.

When an enterprise customer's IT administrator searches the Marketplace, finds Clearledgr, reads Google's security review results, and approves the extension for their domain — the extension can be deployed to all finance team members simultaneously. Without the Marketplace listing, every individual user installs the extension themselves, which many enterprise IT policies prohibit. The Marketplace listing is the difference between Clearledgr being deployable to enterprise and not.

The Workspace Add-on for mobile approvals is also listed in the Marketplace, separately from the Chrome extension. Both must be listed, reviewed, and approved by Google before enterprise distribution is possible.

---

## 7. The Agent

The agent is the product. The surfaces are delivery mechanisms. Every design decision about how the agent communicates, what it acts on autonomously, and how it escalates determines whether finance teams trust it enough to grant it expanding permissions over time.

### 7.1 The Communication Pattern

Every agent action — autonomous or escalated — follows one pattern without exception. It is three sentences. No more.

> **DID — WHY — NEXT**
>
> *'Matched INV-2841 to PO-2041. Three-way match complete within 0.3% tolerance. Routed to Sarah Chen for approval.'*

This pattern is not a UX convention. It is the mechanism by which trust accumulates. A system that acts without explaining itself will be overridden by a suspicious finance team. A system that explains its reasoning in consistent, specific language builds credibility with every invoice it processes. After ninety days, the AP Manager stops checking the reasoning for routine matches. That is the trust milestone that unlocks expanded autonomous thresholds.

### 7.2 Autonomy Tiers

The agent operates across three tiers of autonomy, governed by configurable thresholds set during onboarding and expanded as performance is demonstrated.

| Tier | Agent acts on | Human role |
|------|---------------|------------|
| Autonomous | Invoice parsing, 3-way match execution, ERP data retrieval, vendor master lookup, vendor onboarding email dispatch, KYC document requests, micro-deposit initiation, duplicate detection. | Notified after the fact via timeline entry. Can override within a configurable window. |
| Supervised | Approval routing, payment scheduling, vendor activation in ERP, stage transitions on exception resolution, first payment to a new vendor below threshold. | Receives Slack notification. Approves or rejects. Agent executes on confirmation. |
| Human-led | Threshold overrides, CFO-level sign-offs, new vendor payments above limit, policy exceptions, invoices with no matching PO and no procurement context. | Initiates and decides. Agent prepares all context, surfaces the decision, and executes the instruction once given. |

Autonomy tiers expand as trust accumulates. A new customer onboards with most actions in the Supervised tier. After thirty days of accurate processing, the agent presents a tier expansion recommendation with performance data. The finance team decides whether to accept. This is the mechanism that drives both retention and ACV expansion — trust, earned through performance, unlocks more automation, which delivers more value, which justifies a higher renewal price.

### 7.3 Tone and Language

The agent communicates like a precise, senior finance colleague. Not a chatbot and not a system notification. Three rules govern every agent-generated message:

- **Specific, never vague.** 'Invoice €8,922 vs GRN receipt €8,500 — delta €422 exceeds 2% tolerance' not 'amount mismatch detected.'
- **Finance language, not product language.** '3-way match' not 'document verification'. 'GRN' not 'receipt confirmation'. 'Net 30' not 'payment timeline'. 'IBAN' not 'bank account number'.
- **Short.** One sentence for the action. One sentence for the reason. One sentence for what is next. Any agent message longer than three sentences is a failure of design, not a richness of information.
- **No filler.** The agent does not say 'Hi there' or 'Great news'. It starts with the fact.

### 7.4 The Confidence Model

The agent's confidence in a given action exists on a spectrum. The product must represent this honestly rather than presenting all actions with equal certainty.

- **High confidence:** act autonomously and log it. Do not interrupt the finance team.
- **Medium confidence:** act, surface the reasoning prominently, and shorten the override window to 15 minutes.
- **Low confidence:** do not act. Present the full context and request a decision. Include what information would resolve the uncertainty.

Overconfidence is more dangerous than under-confidence in a finance product. A duplicate payment caused by an overconfident agent ends the customer relationship. The product is calibrated to escalate rather than guess at the margin of its confidence.

### 7.5 The Trust-Building Arc

Streak earns trust in the first hour. A new user installs the extension, creates a pipeline, adds a deal, and immediately sees the label appear in their inbox and the sidebar load with the right data. The feedback loop is instant. Trust follows.

Clearledgr earns trust differently. The feedback loop is thirty days, not thirty seconds. The AP Manager needs to see the agent process twenty invoices correctly before they stop checking every match result. They need to see the agent flag the right exception — and only the right exception — before they trust the exceptions they do not see. They need to see the override window close on an autonomous ERP post and know the payment went through correctly before they grant a higher threshold. This trust arc is longer, more fragile, and must be deliberately designed. It does not happen passively.

#### Week One — Show Everything

In the first week, the agent operates in maximum transparency mode regardless of autonomy tier settings. Every action — including actions that would normally be silent in the Autonomous tier — generates a visible Box timeline entry with full reasoning. The AP Manager is not expected to review all of these. But they must be available.

The goal of week one is not to impress — it is to make the agent's behaviour completely legible. If the AP Manager looks at any invoice Box at any point in week one, they must be able to reconstruct exactly what the agent did and why from the timeline alone. Opacity in week one ends the relationship.

Clearledgr Home in week one shows a persistent banner: 'Agent in observation mode — all actions are visible in Box timelines. Override window extended to 30 minutes.' This is not a warning. It is an invitation. The AP Manager is being told: watch, correct if needed, and see that the agent handles it. The message is: trust the process, not yet the result. The result will earn trust on its own.

#### Week Two — Establish the Baseline

By Day 14, the agent has processed enough invoices to establish the customer's exception rate baseline. On Day 14, the AP Manager receives a single Slack message: 'In your first two weeks, the agent processed 47 invoices. 43 matched cleanly and required no action from you. 4 were exceptions — all of which you resolved. Your baseline exception rate is 8.5%. The industry average is 12%. Full breakdown in Agent Activity.'

This message does two things. It makes the agent's performance concrete and comparable. And it makes the AP Manager aware — possibly for the first time — of how much work the agent has already done on their behalf. 43 invoices they did not touch. 43 PDFs they did not read. 43 ERP records they did not check. The trust arc is partly about accuracy. It is also about making the volume of invisible work visible at the right moment.

#### Day 30 — The Tier Expansion Conversation

On Day 30, the agent sends a structured message to the AP Manager in Slack. Not a notification — a conversation. 'You have been on Clearledgr for 30 days. 94 invoices processed. 89 matched cleanly. 5 exceptions. Match accuracy: 98.9%. Exception override rate: 0% — every exception flagged, you agreed with. Zero duplicate payments. Zero late payments caused by processing delay. Based on this, the agent recommends moving three action types from Supervised to Autonomous: payment scheduling under £10,000, vendor onboarding stage transitions, and ERP posting within tolerance. Accept all, accept individually, or keep current settings. [Review recommendations]'

This message is the trust arc's culmination in V1. It is designed to feel earned, not pushed. The AP Manager has spent thirty days watching correct behaviour. The recommendation is not a sales motion — it is a logical next step the data supports. The AP Manager who says yes is not taking a leap of faith. They are making a rational decision based on thirty days of evidence.

#### The Ongoing Signal

After Day 30, trust continues to accumulate through a single weekly signal — not a dashboard, not a report. Every Monday morning in Slack: 'Last week: 23 invoices processed, 0 exceptions you disagreed with, £187,400 in payments scheduled correctly. Agent accuracy: 100%.'

When that number is consistently high, the AP Manager stops reading it. That is the goal. The weekly signal exists not to be read but to be ignored — because ignoring it means the agent is trusted completely. The day the AP Manager stops ignoring it is a signal to the Clearledgr team that something has changed. It is an early warning system disguised as a summary.

> *Trust in an autonomous finance agent is not granted. It is accumulated, invoice by invoice, exception by exception, over thirty days of correct behaviour. The product must design for that accumulation as deliberately as it designs any surface. An agent that performs correctly but does not make its performance visible will never be granted the permissions that make it truly useful.*

### 7.6 LLM Failure Modes and Deterministic Guardrails

The agent uses a large language model for invoice extraction, exception reasoning, and natural language communication. LLMs are powerful and imprecise. A hallucinated invoice amount posted to NetSuite is not a UX problem — it is a payment failure that ends the customer relationship. Finance is a domain where a single wrong decimal in an autonomous action causes real financial loss. This demands a different approach to LLM deployment than most products.

The architecture is: **deterministic guardrails constrain the LLM's judgment.** The LLM reasons within boundaries set by rules. It never acts beyond what the rules permit, regardless of its reasoning.

#### Extraction Guardrails

Invoice data extraction is the highest-risk LLM task. The agent reads a PDF or email body and extracts structured fields: vendor name, invoice reference, amount, due date, line items, PO reference. Every extracted value is subject to deterministic validation before it is acted upon.

| Guardrail | What it prevents |
|-----------|------------------|
| Amount cross-validation | The extracted amount is compared against any amount visible in the email subject, body, and attachment independently. If they disagree — even by a single character — the agent raises a low-confidence flag and does not proceed to matching. It surfaces both values and asks the AP Manager to confirm which is correct. |
| Currency consistency | The extracted currency is validated against the vendor's configured currency in the ERP. A EUR invoice from a GBP vendor is flagged immediately. The agent does not convert or assume — it stops and asks. |
| Reference format validation | Invoice references are validated against the format pattern established from this vendor's historical invoices. A vendor who always uses INV-XXXX format triggering an extraction of PO-2041 is flagged as a possible extraction error. |
| Amount range check | The extracted amount is compared against this vendor's historical invoice range. An invoice for £1,200,000 from a vendor whose largest previous invoice was £12,000 triggers a mandatory human review regardless of match result. It may be correct — but the agent cannot act on it without explicit confirmation. |
| PO reference existence check | The extracted PO reference is validated against the ERP before any matching begins. If the PO does not exist, the agent stops immediately. It does not attempt to find a close match or guess an alternative — it reports the exact PO number from the invoice and the fact that it does not exist in the ERP. |

#### Matching Guardrails

3-way match is deterministic, not LLM-driven. The match logic is a set of explicit rules applied to structured data: invoice amount vs GRN amount within tolerance, invoice PO reference vs ERP PO record, GRN confirmation date before invoice due date. The LLM's role in matching is only to write the plain-language exception reason — it does not determine whether a match passes or fails.

This distinction is foundational. The AP Manager reading 'Invoice £8,922 vs GRN receipt £8,500 — delta £422 exceeds 2% tolerance' must be able to trust that the numbers are correct and the logic is rule-based. If the match result were LLM-generated, that trust would require explaining the model's reasoning. If the match result is rule-based and the LLM only writes the description, the AP Manager can verify the arithmetic themselves. Transparency is built into the architecture, not bolted on.

#### Posting Guardrails

No autonomous ERP post happens without a pre-post validation check. Before writing to NetSuite or SAP, the agent re-reads the extracted data from its own extraction record and validates it against the ERP's current state: the PO is still open, the GRN is still unmatched, the vendor is still active, the payment has not already been scheduled. This is a defence against race conditions — two emails arriving from the same vendor within minutes, both processed by the agent, potentially creating a duplicate post.

Duplicate detection runs at the Vendor level across a configurable window (default 90 days). If an invoice reference, amount, and vendor combination matches any Box in the window, the agent blocks the post and flags for human review. It never silently processes a potential duplicate.

#### The Audit Trail as Proof

The question an auditor asks about every autonomous agent action is: how do we know this was correct? The answer must not be 'trust the model.' It must be a verifiable record of what the agent saw, what rules it applied, and what it concluded.

Every agent action writes three things to the Box timeline: the raw extracted data (exactly what the agent read from the invoice), the rule applied (exactly which guardrail or match rule produced the outcome), and the conclusion (what the agent did and why in plain language). An auditor can reconstruct every autonomous decision from the timeline without speaking to the Clearledgr team. The audit trail is not a compliance feature — it is the evidence that the system is trustworthy.

#### What Happens When the Agent Is Wrong

The agent will be wrong. The question is not whether — it is how often, in what direction, and how quickly it is caught and corrected.

- **When the agent extracts incorrectly:** the guardrails catch it before it reaches matching. The AP Manager sees a flagged extraction with both the raw value and the extracted value. They correct it. The correction is logged. The agent's confidence threshold for this document type is lowered temporarily.
- **When the agent matches incorrectly:** the AP Manager's override is logged with their reasoning. The override pattern is reviewed in the Backoffice agent performance dashboard. If overrides cluster around a specific exception type, vendor, or document format, it flags a model or policy issue for the engineering team.
- **When the agent posts incorrectly:** the override window is the last line of defence. If the window closes before the error is caught, the correction requires a manual ERP adjustment — not something the agent can do. This is the scenario the guardrails are designed to make impossible. A posted amount that differs from the approved amount by more than tolerance is a P0 incident on the Backoffice engineering dashboard, not just a customer support ticket.
- **Mass failure scenario:** if the agent processes a batch of invoices with a systematic extraction error — a model update that misreads a specific PDF format — the override window exists for each post, but the batch nature means some may close before the error is noticed. The Backoffice flags automated: 'Exception override rate has increased 300% in the last 4 hours' and pages the engineering on-call. No model deployment happens in a batch window where the agent is actively processing invoices.

### 7.7 Testing and QA Strategy

An agent that writes to production ERPs cannot be tested only in staging. The ERP is the system of record. A test environment that does not reflect the actual state of the customer's NetSuite, SAP, or Xero will produce test results that do not reflect production behaviour. This is the fundamental QA challenge for Clearledgr: how do you gain confidence in a model change before deploying it to a system that controls real payments?

#### The Testing Layers

| Layer | What it covers and how it works |
|-------|----------------------------------|
| Synthetic invoice test suite | A library of synthetic invoices covering every document format, edge case, and failure mode the team has observed. This library is a trajectory, not a day-one baseline: at launch it contains the formats encountered during implementation and early customer onboarding. It grows with every new edge case encountered in production — each extraction failure that reaches the Known Issues board adds at least one new synthetic test case. The target floor is 500 invoices across all supported formats and ERP connectors before the product is considered at general availability maturity. New extraction model versions run against the full suite before any deployment. Pass rate must equal or exceed the previous version on every document category. A regression on scanned PDFs blocks deployment even if overall accuracy improves. |
| Historical replay testing | Every invoice processed in production generates a stored replay record — not the original PDF (which remains in Gmail and is never stored by Clearledgr), but the extracted JSON fields, the anonymised raw OCR text, and the confirmed outcome (what the AP Manager accepted or corrected). New model versions are run against this replay dataset. Their output is compared to what the production model produced and what the AP Manager confirmed. Disagreements between the new model and confirmed-correct historical outcomes are reviewed manually before deployment. Storing extracted JSON and anonymised text rather than PDFs satisfies both the replay testing requirement and §19's data minimisation principle — the attachment itself never leaves Gmail. |
| Shadow mode deployment | New model versions run in shadow mode on live production traffic for a minimum of 48 hours before any customer-facing change. In shadow mode, the new model processes every incoming invoice and produces an output, but that output is never shown to the customer and never used to make any decision. The shadow output is compared to the production model output. Disagreements above a threshold block promotion out of shadow mode. |
| Canary deployment | After shadow mode, the new model is promoted to a canary cohort — a small set of Starter customers on lower invoice volumes, with explicit informed consent at onboarding that they participate in canary rollouts. Canary customers see the new model's output through the normal product interface. Exception rates, override rates, and AP Manager feedback are monitored for 72 hours. If any metric degrades, the canary is rolled back before wider deployment. |
| Full deployment with monitoring | After canary, the model is deployed to all customers with a 24-hour elevated monitoring window. The Backoffice exception rate and override rate dashboards are live-watched by the on-call engineer. Any statistically significant increase in either metric triggers an immediate rollback. |

#### The Deployment Freeze Window

No model deployment happens during a period when the agent is actively processing a high invoice volume. Deployment windows are: Tuesday through Thursday, between 10am and 2pm UK time. This avoids Monday morning invoice rushes, Friday payment run finalisation, and overnight batch processing windows. The deployment schedule is published to customers on Professional and Enterprise plans so their AP teams know when to expect a potential brief processing pause.

#### ERP Write Testing

ERP writes are the highest-risk operations in the system. Before any new ERP connector version is deployed, a full write-and-rollback test runs against a dedicated test company within the target ERP. The test posts a synthetic invoice, validates the post, then reverses it. Success requires the post and reversal to complete cleanly, with the ERP returning to its pre-test state. Connectors that cannot demonstrate clean reversal are not deployed to production.

### 7.8 Disaster Recovery and Rollback

The question is not whether the agent will make a systematic error at some point. It will. The question is: what is the designed response when it does, and how quickly can the damage be contained and reversed?

#### Scenario: Systematic Extraction Error

A model update misreads a specific PDF format — perhaps a vendor has changed their invoice template and the new layout confuses the extraction layer. The agent processes 15 invoices from vendors using that format, extracts wrong amounts on all of them, and posts all 15 to the ERP before the error is detected.

**Detection:** the Backoffice exception override rate monitor fires within 4 hours. AP Managers reviewing the invoices override the match results at an elevated rate. The engineering on-call is paged.

**Immediate response:** all unprocessed invoices from the affected document format are held immediately — no new posts while the investigation runs. The on-call engineer identifies the 15 affected invoices from the deployment log and the timeline of when the model was promoted.

**Rollback:** the previous model version is restored within 15 minutes via feature flag. No redeployment required. The 15 affected invoices are flagged in the Backoffice as requiring manual review. Their ERP posts are reversed using the ERP connector's reversal API, which every connector is required to support before deployment.

**Recovery:** a Clearledgr CS Manager contacts the affected customers within the hour. The specific invoices are identified, the correct amounts confirmed with the AP Manager, and the invoices are reprocessed through the restored model. A post-incident report is published to the customer within 24 hours explaining what happened, what was affected, and what was done to prevent recurrence.

#### Scenario: Mass Duplicate Payment

A race condition in the duplicate detection logic causes the agent to process 8 invoices that were already paid in the previous period. The ERP posts go through. The AP Manager notices when reviewing the payment run and escalates.

**Containment:** the payment scheduling step — which happens after ERP posting — is gated on a human confirmation for all payments above a configurable threshold. If the duplicates were scheduled but not yet transmitted to the bank, the schedules are cancelled immediately. If they were transmitted, the bank's payment cancellation window applies — typically 30 minutes for SEPA.

**The lesson:** the ERP post and the payment execution are distinct steps. ERP posting is a record-keeping action. Payment execution is the irreversible step. Clearledgr's architecture maintains this separation explicitly so that a posting error does not automatically become a payment error.

#### The Rollback Guarantee

Every autonomous agent action that writes to an external system — ERP post, email dispatch, Slack message — is logged to the Box timeline before it executes, not after. If the action fails partway through, the timeline shows what was attempted, what completed, and what did not. There is no silent partial success. The engineering team can reconstruct the exact state of any Box at any point in time from its timeline.

For ERP writes specifically: the connector writes to the ERP and receives a confirmation before updating the Box stage. If the ERP confirmation is not received, the Box stage does not change and the invoice remains in its previous state. The agent does not assume success. It requires confirmation.

> *The override window is the last human escape hatch for autonomous actions. The ERP write separation from payment execution is the architectural escape hatch. The rollback guarantee is the audit escape hatch. Three independent lines of defence, each designed to catch what the previous one misses.*

### 7.9 The Model Improvement Loop

The agent gets better over time — but only if the improvement loop is designed. Left to itself, an LLM does not learn from its mistakes. The model improvement loop is the system that turns every override, every correction, and every AP Manager decision into a signal that improves future performance.

#### Signal Collection

Every AP Manager action that diverges from the agent's output is a learning signal. The agent proposed a match result — the AP Manager overrode it. The agent extracted an amount — the AP Manager corrected it. The agent classified an email as an invoice — the AP Manager reclassified it as a credit note. These divergences are logged automatically to the Backoffice agent quality dashboard with three fields: what the agent produced, what the human confirmed, and the document type and vendor category. No additional data entry is required from the finance team.

#### The Improvement Flywheel

Signal collection alone does not improve the model — it creates a dataset. The improvement flywheel is the process that turns the dataset into better model behaviour.

| Step | What happens |
|------|--------------|
| Signal accumulation | Override and correction signals accumulate in the Backoffice quality dataset, tagged by document type, vendor category, ERP connector, and error category. Minimum 50 signals per category before a pattern is considered actionable. |
| Pattern identification | The engineering team reviews the quality dashboard weekly. Clusters of overrides on a specific document format, vendor, or error type are candidates for targeted improvement. A vendor who generates overrides 30% of the time is a different problem from a document format that generates overrides 80% of the time. |
| Prompt and rule refinement | Most extraction improvements do not require model retraining. They require better prompts, more specific extraction rules, or additional guardrail conditions. These are tested against the historical replay dataset before deployment. |
| Model-level improvement | For persistent extraction patterns that prompt engineering and few-shot examples cannot resolve — typically specific document layouts, unusual currency formats, or non-standard invoice structures — the correction signals are used to build a targeted example library. These examples are incorporated into the system prompt as few-shot demonstrations for future similar documents. If Clearledgr's inference stack moves to a model where fine-tuning is available (open-weights models such as Mistral or Llama variants), correction signals can become fine-tuning examples. With Claude as the inference model, the improvement path is prompt engineering, few-shot libraries, and customer-specific extraction rules — not weight updates. The document uses these terms precisely: the model weights do not change; the instructions and examples the model receives do. |
| Customer-specific learning | Some vendors have idiosyncratic invoice formats that are specific to one customer relationship. Customer-specific extraction rules are stored per-vendor in the Vendor record and applied before the general model. An AP Manager who corrects the same extraction error on the same vendor three times triggers a customer-specific rule being written automatically. |
| Closed-loop validation | After each model improvement, the Backoffice tracks whether the override rate for the targeted category decreases in the four weeks following deployment. If it does not, the improvement is rolled back and the pattern is re-examined. The flywheel only turns if each iteration demonstrably improves the outcome. |

The improvement loop has one important constraint: the finance team's corrections are signals, not labels. The AP Manager who overrides a match result may be wrong — they may be approving a duplicate or overriding a correct exception for convenience. The engineering team reviews override patterns for anomalies before treating any signal as ground truth. Override rate is a metric of model performance and a metric of finance team behaviour simultaneously.

Early customers experience the cold flywheel. The 50-signal minimum per category before a pattern is considered actionable is intentional — acting on fewer signals risks overfitting to one customer's idiosyncratic invoice formats, which degrades performance for everyone else. The practical consequence: the first customers on the platform will see slower model improvement in their specific edge cases than customers who join after the signal corpus has depth. This is not a defect — it is the correct tradeoff between individual responsiveness and fleet accuracy. Early customers are compensated by priority access to the implementation team and direct communication from the engineering team when their specific edge cases are resolved.

---

## 8. Fraud Controls

Finance is the highest-value attack surface in any organisation. Business Email Compromise (BEC), vendor impersonation, IBAN swap fraud, and fake invoice scams all target AP operations. An agent that autonomously processes invoices and schedules payments is an exceptional tool for an AP team — and a lucrative target for a fraudster who understands how it works. Fraud controls are not a feature. They are foundational. The agent should not be able to act on a fraudulent instruction regardless of how convincing the email looks.

### The Primary Attack Vectors

| Attack type | How it works and how Clearledgr defends |
|-------------|------------------------------------------|
| IBAN swap fraud | The most common AP fraud. Attacker compromises a vendor email account or sends a spoofed email instructing the AP team to update the vendor's bank account. If the team processes the update manually, the next payment goes to the attacker. Clearledgr's defence: IBAN changes trigger an immediate payment hold for the affected vendor — no payment is scheduled to any new IBAN until the change is verified. Verification requires: the change instruction to come from the vendor's verified email domain, a phone confirmation to the vendor's registered contact number (not any number provided in the email), and AP Manager sign-off. Three factors, not one. |
| Business Email Compromise (BEC) | Attacker impersonates a trusted person — CEO, CFO, a vendor's finance contact — and instructs the AP team to process an urgent payment. Classic BEC targets humans. Against Clearledgr: the agent does not act on verbal or email instructions from internal senders. All payment instructions flow through the standard AP pipeline. An email from 'CEO@company.com' saying 'please pay this supplier immediately' is classified as a Finance/Query, not actioned autonomously, and the AP Manager is alerted with a fraud risk flag. |
| Fake invoice injection | Attacker creates a convincing invoice from a real or near-real vendor name and sends it to the AP inbox. Against Clearledgr: the vendor must be Active in the vendor master before any invoice from them is processed. An invoice from a sender not in the master is classified as Clearledgr/Review Required and flagged for the AP Manager. The agent never processes an invoice from an unknown sender. Domain similarity detection flags 'str1pe.com' emails when 'stripe.com' is in the vendor master. |
| Internal fraud — rogue employee | An AP team member creates a fictitious vendor, adds them to the vendor master, and submits invoices. Against Clearledgr: vendor activation in the ERP requires AP Manager sign-off as a minimum, CFO sign-off above the first payment limit. New vendor first payments require explicit human approval regardless of invoice value. The full vendor activation audit trail is preserved in the Box timeline and exportable for audit. |
| Invoice manipulation — PDF tampering | Legitimate vendor PDF is intercepted and amount or IBAN is altered before it reaches the AP inbox. Against Clearledgr: the extraction guardrails cross-validate extracted amounts against multiple locations within the document. An altered PDF where the header amount differs from the line item total triggers an extraction flag. Additionally, amounts extracted from emailed PDFs are compared against the amount in the email body where visible — most vendor systems include both. |

### Anti-Fraud Primitives

These controls run before the agent takes any action. They are not configurable by the customer — they are architectural.

| Primitive | What it does |
|-----------|--------------|
| Vendor domain lock | Once a vendor is Active, their invoices are only processed from the email domain registered during KYC. An invoice from `billing@stripe-payments.com` when `stripe.com` is the registered domain is flagged as a domain mismatch and not processed, regardless of how legitimate the invoice looks. |
| First payment hold | Every new vendor's first payment requires explicit human approval regardless of invoice value and match result. The agent never autonomously executes a first payment to a vendor it has never paid before. |
| IBAN change freeze | Any change to a vendor's registered IBAN freezes all outgoing payments to that vendor until the change is verified through the three-factor process. The agent cannot schedule any payment to a new IBAN. |
| Payment amount ceiling | No single autonomous payment may exceed the configured ceiling without CFO approval. The ceiling is set per vendor and globally in Settings. It cannot be overridden by the agent — only by a human with CFO-level permissions in that session. |
| Duplicate payment prevention | Vendor-level duplicate detection across a 90-day window. Same vendor, similar amount, same reference — automatic hold and human review. The agent never posts a potential duplicate to the ERP. |
| Velocity monitoring | If the same vendor submits more than a configured number of invoices within a rolling 7-day window, all invoices beyond the threshold are flagged for human review. Sudden invoice velocity spikes from a vendor are a fraud indicator. |
| Internal instruction rejection | Emails from internal sender addresses instructing the agent to process payments, change vendor details, or override match results are classified as Finance/Query and never actioned autonomously. The agent only takes payment instructions from the standard invoice pipeline. |

> *Fraud controls must be architectural, not configurational. A finance team under pressure to process a payment quickly may be tempted to disable a fraud control. The controls that matter most — IBAN change freeze, first payment hold, domain lock — cannot be disabled by the AP Manager. They can only be modified by the CFO role, with every modification logged to the audit trail.*

---

## 9. Vendor Onboarding Pipeline

Vendor onboarding is where AP breaks down before it begins. Most companies manage new vendors through email chains, shared spreadsheets, and manual ERP entry — a process that takes two to six weeks and involves multiple stakeholders. Clearledgr automates the entire journey from invitation to activation. The finance team should not need to email the vendor directly at any stage.

### The Four Stages

| Stage | What happens |
|-------|--------------|
| Invited | Agent dispatches a personalised onboarding portal link to the vendor contact. Monitors for portal access. Auto-chases at 24h and 48h with no response. Escalates to the AP Manager if 72h pass with no response. |
| KYC | Vendor submits business details, director ID, and certificate of incorporation via the portal. Agent validates document completeness and runs basic checks. Requests specific missing items by name, not by category. |
| Bank Verify | Agent initiates two micro-deposits to the vendor's provided IBAN. Vendor confirms the exact amounts via portal. Account is validated. IBAN on file is marked Verified. Agent will not schedule any payment to an unverified IBAN. |
| Active | Agent writes the vendor to the ERP vendor master with AP-enabled status. The vendor is now capable of submitting invoices that will be processed. Agent posts a confirmation to the finance team's Slack channel. |

### Design Rules for Vendor Onboarding

- Every blocker must be named specifically. 'Certificate of incorporation not yet uploaded' not 'KYC pending'.
- The IBAN displayed in the sidebar and in Slack must always be the vendor-provided one — never auto-filled from any other source. The agent confirms it, the human has visibility.
- Every action timestamp is preserved as the audit trail. Invite sent at 09:12. Document received at 14:30. Micro-deposit dispatched at 15:05. Vendor activated at 09:05 the following day.
- The agent chases vendors. The finance team does not. If a vendor is slow, that is surfaced as a blocker in the Kanban card — not as a task assigned to a human.

---

## 10. Visual Language

The visual language of Clearledgr injections must feel native to Gmail while being distinctly identifiable as Clearledgr. It should feel like Gmail promoted to professional-grade. Not a consumer tool overlaid on enterprise software, and not an enterprise tool that breaks Gmail's visual coherence.

### Colour Semantics

Colour in Clearledgr carries meaning. It is never decorative. Every team member — AP clerk, AP Manager, Controller, CFO — must be able to read colour as status without a legend.

| Colour | Meaning — used for nothing else |
|--------|----------------------------------|
| Green (`#00B87A`) | Passed, verified, active, approved. 3-way match passed. Vendor IBAN verified. Invoice approved. |
| Amber (`#D97706`) | Requires attention. Exception flagged. Vendor onboarding blocked. Approval overdue. |
| Red (`#DC2626`) | Failed or blocked. Match failed. Vendor rejected. Payment blocked. No PO found. |
| Blue (`#1A73E8`) | In progress, scheduled. Invoice scheduled for payment. Approval in transit. |
| Grey | Neutral or not yet processed. Received but not yet matched. Invited but not yet responded. |

### Typography

- **Display and body text:** Georgia — legible, authoritative, at home in a professional document context.
- **Data fields:** IBM Plex Mono — all numbers, invoice references, IBANs, PO numbers, amounts, dates. Monospace makes scanning fast and errors immediately visible.
- **Labels and navigation:** Google Sans — the same typeface Gmail uses. Nav items and toolbar elements use Gmail's own font to feel native.

### The Agent Signature

The lightning bolt (⚡) marks agent-initiated actions throughout the product. It appears in the timeline next to every autonomous action. It appears in the Slack message header for agent notifications. It appears in the sidebar section title for agent actions. Finance teams learn to read it as a visual signal: 'the agent did this, not a human' — a distinction that matters for audit and trust.

---

## 11. What Success Looks Like

A Clearledgr customer is using the product well when the following conditions are true simultaneously. These are not aspirational targets. They are the operational definition of product-market fit for V1.

- Fewer than 10% of invoices require direct human action in the Gmail thread. The agent handles the rest.
- The AP team has not sent an email to a vendor asking for a missing document in 30 days. The agent chased it.
- Average time from invoice receipt to payment approval is under four hours for matched invoices.
- A new vendor went from invited to active in under five business days, with zero manual steps from the AP team.
- The CFO can describe the AP pipeline status — volume, exceptions, scheduled payments — in a two-minute conversation, using only what they have seen in Gmail and Slack.
- When asked to explain how their AP process works, the finance team describes a workflow and not a tool. Clearledgr is invisible enough that the process and the product are indistinguishable.

> *The ultimate measure is adoption depth, not breadth. One finance team for whom Clearledgr has become the invisible backbone of their AP operation is worth more than ten teams who use it occasionally alongside their existing process.*

---

## 12. What We Are Not Building

Clarity about what Clearledgr is requires equal clarity about what it is not — including clarity about who it competes with.

### The Competitive Landscape

The tools Clearledgr competes with are not Streak. They are Bill.com, Ramp's AP module, Stampli, Tipalti, and Airbase. These are the tools a mid-market CFO already uses or is evaluating when Clearledgr enters the room. The Gmail-native agent story needs a direct answer to each of them.

| Competitor | Why a mid-market CFO picks Clearledgr |
|------------|----------------------------------------|
| Bill.com | Bill.com is a portal. The finance team leaves their inbox, logs into Bill.com, manually processes invoices, and comes back. Clearledgr brings the processing to the inbox and automates it. The CFO's pitch: 'Your AP team currently switches context 40 times a day between Gmail and Bill.com. With Clearledgr, they do not switch at all.' The commercial question is whether Gmail-native automation delivers better adoption and lower processing cost than a purpose-built AP portal. We believe it does — the evidence is in Streak's adoption rate vs Salesforce's in the SMB market. |
| Ramp AP Module | Ramp's strength is spend management — cards, expenses, vendor payments as a single suite. Their AP module is secondary to the card product. Clearledgr is AP-first and agent-first. The CFO's pitch: 'Ramp gives you a dashboard to check. Clearledgr gives you an agent that handles it.' The weakness to address: Ramp customers are already in a spend management ecosystem. Clearledgr needs to show that the agent generates enough value on AP alone to justify running alongside Ramp rather than replacing it. |
| Stampli | Stampli's differentiator is collaborative invoice management — a comment layer on top of AP. Clearledgr's differentiator is the agent eliminating the collaboration requirement. The CFO's pitch: 'Stampli makes it easier to discuss invoices. Clearledgr makes the discussion unnecessary for 90% of them.' Stampli is the most direct process overlap — both target AP teams, both live adjacent to the inbox. The key question is whether collaborative visibility (Stampli) or autonomous execution (Clearledgr) wins with the mid-market CFO. |
| Tipalti | Tipalti owns global mass payments — contractor payouts, affiliate networks, multi-currency at scale. The overlap with Clearledgr is narrow: standard vendor AP. The CFO's pitch: 'Tipalti is built for paying thousands of contractors. Clearledgr is built for the 50-500 vendor relationships that run through your AP inbox every month.' These products can coexist — Tipalti handles high-volume, low-touch payments; Clearledgr handles standard AP with full agent processing and audit trail. |
| Existing ERP workflows | NetSuite, SAP, and Xero all have AP modules. The CFO who says 'we already do AP in NetSuite' is the most common objection. The answer: Clearledgr does not replace NetSuite AP — it automates the work that happens before NetSuite is involved. The invoice arrives in email. The 3-way match happens in email. The approval happens in Slack. NetSuite receives the post-match, post-approval result. Clearledgr is the execution layer on top of the ERP, not a replacement for it. |

The consistent competitive frame: every competitor asks the finance team to go somewhere else. Bill.com is a portal. Ramp is a dashboard. Stampli is a collaboration layer. Tipalti is a payments platform. NetSuite is an ERP module. Clearledgr is the only one that stays where the work already is. That is the answer to every competitive question in the room.

### What We Are Deliberately Not Building

These are deliberate choices that protect the thesis — not limitations to be overcome in later versions.

- **Not a web application (in V1).** There is no Clearledgr.com dashboard. There is no separate URL the finance team visits to do their AP work. If a finance workflow requires leaving Gmail, we have failed to design the Gmail surface correctly.
- **Not a system of record.** We read from and write to the ERP. Financial data lives in NetSuite or SAP. We do not store it. We do not migrate it. We do not ask finance teams to trust a new database with their numbers.
- **Not a chatbot.** The agent does not wait to be queried. It acts. The natural language query field in the sidebar is for ad hoc questions, not for daily workflow. The agent's job is to have already done the thing before the user thinks to ask about it.
- **Not built for SMBs first.** The product is designed for mid-market and enterprise finance teams — teams with invoice volume, approval hierarchies, ERP systems, and audit requirements. Consumer-grade simplicity is not the target. Professional-grade precision is.
- **Not a tool the CFO buys because it looks impressive.** The buying decision is made because it measurably reduces time-to-payment, eliminates duplicate payments, compresses the close cycle, and reduces the AP headcount required per invoice volume. The design serves those outcomes. It does not substitute for them.
- **Not an Outlook product.** Clearledgr V1 requires Google Workspace. The Chrome extension, InboxSDK, Gmail's left nav injection, the thread sidebar, the toolbar buttons — all of this is Google infrastructure. Enterprise procurement will ask whether Clearledgr supports Outlook. The answer in V1 is no. This is not an oversight. Outlook's extension model, permissions framework, and DOM access patterns are different enough that building for both simultaneously would compromise the depth achievable in Gmail. Clearledgr does Gmail exceptionally. It does not do Outlook at all. This boundary must be stated explicitly in every sales conversation to avoid a mismatch that kills a deal at the procurement stage.

---

## 13. Subscription Model

Streak's pricing page says exactly this: 'If you're ready to sign up for a paid plan, install Streak, click the Streak button on the top right of Gmail and select Upgrade.' No separate website to log into. No account portal to navigate. Billing, team management, and subscription changes all happen from within Gmail. Clearledgr is built on the same principle. Every customer interaction with Clearledgr — including upgrading, managing seats, and adjusting billing — happens inside Gmail.

The cost structure underlying this pricing model has primary components that must be understood before prices are set, not after reaching Series A. LLM inference is the largest variable cost — every invoice involves at minimum one extraction call and one exception reasoning call if the match fails. Shadow mode testing during model deployments doubles inference cost for 48+ hours per cycle. Historical replay testing runs the new model over the full anonymised corpus, a cost that grows with corpus size. ERP API calls carry connector-specific costs depending on the ERP's API tier. Storage costs for Box timelines and replay records scale with invoice volume and retention period. Agent action credits are the mechanism by which high-inference actions are priced separately from standard processing — they make the variable cost structure visible in the pricing model before it becomes a margin problem. The specific unit economics — cost per invoice at Starter volume versus Enterprise volume, gross margin per tier, break-even invoice count per seat — are maintained in the financial model, not this document.

### The Three Tiers

| Tier | Who it is for | What defines it |
|------|---------------|-----------------|
| Starter | Mid-market finance teams processing up to 500 invoices per month. Single AP pipeline, one ERP connection, Slack integration, core AP and Vendor Onboarding workflows. | Per seat plus invoice volume band. Designed to go live in under 30 minutes with no IT involvement. All settings, billing, and team management accessible within Gmail. Auto-approve threshold up to a configurable limit. Standard match tolerance controls. |
| Professional | Finance teams scaling across AP, AR, Reconciliation, and Month-End Close. Multiple pipelines, advanced approval routing, multi-entity ERP support, analytics dashboards within Gmail, API and webhook access. | Per seat plus invoice volume plus agent action credits. Autonomy tier expansion available after 30-day performance review. Webhooks for custom ERP integrations. Advanced reporting as a Gmail custom route. Priority support. |
| Enterprise | Large finance functions with complex hierarchies, multi-entity structures, compliance requirements, and internal IT security processes. All Professional features plus custom roles, data validation rules, DPA as standard, SOC 2 reporting, dedicated implementation, and a direct line to Clearledgr leadership. | Negotiated annual contract. Custom invoice volume and seat structure. Dedicated account manager. SLA guarantees. Implementation service included. Clearledgr team configures the initial pipeline before go-live. |

### Pricing Structure

Clearledgr prices on two components. The seat charge covers access, roles, the agent running continuously, and all platform features. The volume charge reflects the scale of work the agent is actually doing.

| Component | How it works |
|-----------|--------------|
| Seat charge | Per active user per month. Annual billing saves 20%. All seats within a workspace are on the same tier — no mixing Starter and Professional within one team. Read Only seats for external auditors are charged at a reduced rate and expire automatically after a configurable period. |
| Invoice volume | Charged per invoice processed per month, in bands. The first band is included in the seat charge. Overage is calculated and charged at the end of each billing period. Unused volume does not roll over. |
| Agent action credits (Professional and Enterprise) | A pooled credit system for compute-intensive agent actions: extracting data from non-standard invoice formats, adverse media checks during KYC, multi-entity ERP reconciliation. Credits are pooled across the team, purchased in advance, and consumed per action. Failed actions do not consume credits. A confirmation prompt appears before any action that would consume a significant number of credits. |
| Annual discount | Annual billing saves 20% on the seat charge. Volume bands and agent action credits are priced the same regardless of billing cycle. Enterprise contracts are annual only. |

### Managing Subscription Inside Gmail

The entire subscription lifecycle happens within Gmail via the Clearledgr section in the left nav. There is no clearledgr.com customer portal. There is no separate login. Consistent with the Streak thesis: if it requires leaving Gmail, it should not exist.

| Subscription action | How it works inside Gmail |
|---------------------|---------------------------|
| Upgrade from Starter to Professional | Click the Clearledgr menu in Gmail top bar. Select Upgrade. Choose tier and billing cycle. Enter payment details. Upgrade takes effect immediately. The new features appear in the left nav without restarting Gmail. |
| Add seats | Settings > Team > Add member. Enter email address. Assign role. An invitation is sent. The new seat is billed on the next cycle at a pro-rated amount for the remainder of the current period. |
| Remove a seat | Settings > Team > Remove member. Access is revoked immediately. Their Box timeline entries are preserved — audit history is never deleted when a user is removed. The seat is credited on the next billing cycle. |
| View billing and usage | Settings > Billing. Shows current tier, next renewal date, seat count, invoice volume used this period, agent action credits remaining, and payment history. All within Gmail. |
| Purchase additional agent action credits | Settings > Billing > Buy credits. Select credit bundle. Charged immediately as a one-time add-on. Available to the team pool within minutes. |
| Upgrade to Enterprise | Settings > Billing > Contact us. Opens a Slack message or email to the Clearledgr team directly. Enterprise is not self-serve — it requires a conversation to configure correctly. |

### Tier Feature Comparison

| Feature | Starter | Professional / Enterprise |
|---------|---------|---------------------------|
| AP Invoices pipeline | Included | Included |
| Vendor Onboarding pipeline | Included | Included |
| Gmail sidebar and toolbar injection | Included | Included |
| Slack integration | Included | Included |
| ERP connections | 1 — standard connectors | Multiple — plus custom API |
| Approval routing | Single tier | Multi-tier with escalation and OOO routing |
| Autonomy tier expansion | Fixed — Supervised default | Configurable after 30-day performance review |
| Saved Views — Show in Inbox | 3 per pipeline | Unlimited |
| Agent Activity feed retention | 30 days | Statutory minimum — default 7 years |
| Analytics dashboards within Gmail | Not included | Included |
| API access and webhooks | Not included | Included |
| Custom roles | Not included | Enterprise only |
| Data validation rules | Not included | Enterprise only |
| DPA and SOC 2 reporting | Not included | Enterprise only |
| Implementation service | Not included | Included for Enterprise — available for a fee on Professional |
| Support | Live chat | Priority on Professional — Dedicated with SLA on Enterprise |

### Implementation Service

Streak offers Advanced Implementation as a paid service for Pro and Pro+ customers — five hours of dedicated setup work — and includes it for Enterprise. Clearledgr's Implementation Service follows the same model.

| Implementation scope | What Clearledgr delivers |
|----------------------|--------------------------|
| ERP connection and validation | Configure and test the ERP OAuth connection. Validate that PO, GRN, and vendor master data is accessible and correctly mapped. Run a test invoice through the full match flow before go-live. |
| AP policy configuration | Set auto-approve threshold, match tolerance, and approval routing to match the customer's documented finance policy. Not a generic default — built to their actual rules. |
| Vendor master import | Import existing vendor records into the onboarding pipeline with correct status. Flag vendors with missing bank details for active verification. |
| Team setup and role assignment | Walk each stakeholder through their specific surface: AP Clerk through inbox labels and the sidebar, AP Manager through the approval flow, CFO through the pipeline view and Slack notifications. |
| First invoice batch review | Process the first 20 invoices live with the finance team present. Review every agent action, every exception, every match result. Tune policy settings based on what is found. Clearledgr does not leave until the first batch runs cleanly. |

---

## 14. The Clearledgr Backoffice

`backoffice.clearledgr.com` is Clearledgr's internal operations platform. Customers never log into it and have no reason to know it exists. It is how the Clearledgr team manages every dimension of running the business — customer health, agent quality, implementation delivery, revenue operations, compliance, product rollouts, and system reliability.

A customer who has never heard of `backoffice.clearledgr.com` is having the correct experience. The Backoffice exists to make that experience consistently true.

### Access

Restricted to Clearledgr employees only via Google Workspace SSO. Role-based access within the Backoffice is separate from customer-facing roles. Every action is logged with the identity of the person who took it, the timestamp, and the customer workspace affected. The log is immutable.

### Core Operating Sections

| Section | Primary function |
|---------|------------------|
| Customer workspace management | Per-workspace health score, subscription status, ERP connection, seat utilisation, invoice volume trend, onboarding completion, feature adoption. The CS team's daily operating view. At-risk accounts surface before the customer notices a problem. |
| Agent performance and quality | Match accuracy, false positive and negative rates, extraction accuracy by field and document format, processing time, exception rate, ERP write success rate — all aggregated across the fleet. Drops in accuracy precede support tickets by 24-48 hours. |
| Implementation and onboarding | Kanban of all active implementations, step completion per engagement, benchmark comparison against historical averages, self-serve onboarding health for Starter customers. |
| Support operations | Ticket queue tiered by SLA, full history per workspace, escalation management linked to engineering, known issues board for proactive CS outreach, CEO line for Enterprise escalations. |
| Revenue and billing | MRR/ARR, churn and expansion, renewal pipeline, volume overage alerts, credit consumption, payment failure management. |
| Compliance and audit | Audit log export requests, DPA management, GDPR data subject requests, SOC 2 evidence collection, security questionnaire management, data residency configuration. |
| Product and engineering | Feature flags per workspace, ERP connector health, InboxSDK version monitoring, system health dashboard, deployment log, agent model version tracking. |

> *`backoffice.clearledgr.com` is how the Clearledgr team operates. It is not a product. The measure of its quality is not how impressive it looks — it is how rarely the customer-facing product breaks. Full operational detail is maintained in a separate internal operations document.*

---

## 15. Onboarding

Onboarding is the first interaction Clearledgr has with a finance team's actual data, actual ERP, and actual workflows. It is also the moment of highest abandonment risk. Streak's founding insight on onboarding was that setup friction is the primary reason business tools fail to be adopted — not because the product is bad, but because the cost of getting started exceeds the perceived immediate value.

Clearledgr's onboarding has two modes, and honesty about which applies to which customer is non-negotiable in the sales process.

**Starter — self-serve in under 30 minutes.** Applies to Google Workspace customers on Xero or QuickBooks Online. Both ERPs support OAuth flows that do not require IT administrator consent. The AP Manager can complete the connection, configure policy, and receive the first processed invoice without involving IT or Clearledgr professional services. This is the promise the onboarding section makes, and it holds for this segment.

**Enterprise — managed implementation required.** NetSuite and SAP OAuth flows require administrator consent — a privilege the AP Manager does not typically hold. Write access to post bills requires elevated ERP permissions that finance teams do not self-assign. Enterprise customers on these ERPs require IT involvement for the ERP connection step, and the Implementation Service is mandatory, not optional. Stating this as 'no IT involvement' in a sales conversation to an enterprise customer on NetSuite creates a mismatch that kills the deal at procurement. The honest description: 'Starter customers on QuickBooks and Xero are live in 30 minutes. Enterprise customers on NetSuite and SAP go live through our Implementation Service, typically in one week.' Both are strong outcomes.

### The Four Onboarding Steps

| Step | What happens |
|------|--------------|
| 1 — Install Extension | Finance team member installs the Clearledgr Chrome extension. InboxSDK initialises. Clearledgr section appears immediately in Gmail's left nav. Time: under 60 seconds. |
| 2 — Connect ERP | OAuth connection to NetSuite, SAP, Xero, or QuickBooks via a guided flow inside Gmail. Clearledgr requests read access to POs, GRNs, and vendor master. Write access to post approved invoices. No API keys, no IT ticket. Time: under 5 minutes. |
| 3 — Configure AP Policy | AP Manager sets three values inside Gmail: auto-approve threshold (e.g. invoices under £1,000 that pass 3-way match are approved automatically), match tolerance (e.g. 2% delta acceptable before exception is raised), and approval routing (who receives Slack notifications for which invoice bands). Time: under 10 minutes. |
| 4 — Connect Slack | OAuth connection to the team's Slack workspace. AP Manager selects which channel receives agent notifications. Agent posts a test message confirming connection. Time: under 2 minutes. |

At step four completion, the product is live. The next invoice email that arrives in the AP inbox will be processed automatically. Onboarding is not a project. It is not a workshop. It is not a professional services engagement. It is four steps completed in one sitting.

### Onboarding Design Rules

- Every step must be completable inside Gmail. No external URLs, no separate onboarding portal, no email threads with the Clearledgr team.
- Progress is always visible. The onboarding checklist lives in the Clearledgr nav section until all four steps are complete. Incomplete steps show exactly what is missing and why.
- The agent begins processing immediately at step four — even before the AP Manager has reviewed the first result. Showing early value is more persuasive than any onboarding tutorial.
- If the ERP connection fails, the error message names the specific permission that is missing and links directly to where to grant it in the ERP. Generic 'connection failed' errors are not acceptable.

---

## 16. Settings

Settings in Clearledgr serve one purpose: to make the agent reflect the customer's specific finance policy, not a generic default. Every company has different approval thresholds, different tolerance rules, different team structures, and different ERP configurations. Settings is where those specifics live.

Settings is accessible from the Clearledgr section in Gmail's left nav. It opens as a custom route view inside Gmail — not a new tab, not an external page. It is organised into five sections.

### ERP Connection

| Setting | What it controls |
|---------|------------------|
| ERP platform | NetSuite, SAP Business One, Xero, QuickBooks Online, or Microsoft Dynamics. One connection per workspace. |
| Connection status | Live indicator showing last successful sync, data freshness, and any connection errors with specific resolution steps. |
| Data scope | Which entities the agent reads: PO lines, GRN records, vendor master, chart of accounts, cost centres. Finance team can restrict scope without breaking core AP functionality. |
| Write permissions | Controls whether the agent can post to ERP automatically or must queue for manual posting. Can be set to auto-post for invoices below a threshold and queue above it. |

### AP Policy

| Setting | What it controls |
|---------|------------------|
| Auto-approve threshold | Invoice value below which a passed 3-way match triggers automatic approval and ERP posting without human review. Default: £0 (all invoices require approval). Recommended starting value: £500. |
| Match tolerance | Maximum delta between invoice amount and GRN receipt amount before the agent raises an exception. Expressed as percentage and absolute value. Example: 2% or £50, whichever is lower. |
| Duplicate detection window | Number of days the agent looks back when checking for duplicate invoices from the same vendor for the same amount. Default: 90 days. |
| Payment terms default | Fallback payment terms applied when not specified on the invoice or in the vendor master. Used for due date calculation. |
| Currency handling | Base currency and FX conversion source for multi-currency invoices. Controls how amounts are displayed in the sidebar and compared to PO values. |

### Approval Routing

| Setting | What it controls |
|---------|------------------|
| AP Manager | The team member who receives Slack notifications for all invoices requiring approval. Can be a role shared across multiple people. |
| Escalation thresholds | Invoice value above which approval escalates from AP Manager to Controller, and from Controller to CFO. Each threshold is configurable independently. |
| Slack channel | The channel where the agent posts approval requests and daily digests. Separate channels can be configured for exceptions and approvals. |
| Approval timeout | Number of hours after which an unanswered approval request is escalated to the next tier. Default: 4 hours during business hours. |
| Out-of-office handling | If the assigned approver is marked OOO in Google Calendar, the agent automatically routes to their designated backup. |

### Vendor Onboarding Policy

| Setting | What it controls |
|---------|------------------|
| KYC requirements | Which documents are required for vendor activation: certificate of incorporation, director ID, proof of address, VAT registration. Configurable by vendor country. |
| Bank verification method | Micro-deposit (default) or manual IBAN validation by the AP Manager. Micro-deposit is required for any first payment above the configurable threshold. |
| Auto-chase cadence | How many hours after a missed onboarding deadline before the agent sends a chase email. Default: 24h first chase, 48h second chase, 72h escalation to AP Manager. |
| First payment limit | Maximum value of the first payment to any newly activated vendor. Payments above this limit require CFO approval regardless of invoice value. |

### Autonomy Configuration

| Setting | What it controls |
|---------|------------------|
| Autonomy tier | Current tier per action type: Autonomous, Supervised, or Human-led. The agent presents a tier expansion recommendation after 30 days with supporting performance data. The AP Manager accepts or declines. |
| Override window | Time in minutes during which a finance team member can reverse an autonomous agent action before it becomes irreversible. Default: 15 minutes for ERP posts. Configurable per action type. |
| Agent action log retention | How long the full Box timeline is retained. Default: 7 years (UK statutory minimum for financial records). Cannot be set below the statutory minimum. |

### Automation Rules

Streak has native automations: trigger-and-action pairs configured by users to extend their pipeline beyond the standard workflow. Clearledgr has the equivalent for finance policy — configurable rules that extend the agent's standard AP behaviour to reflect each customer's specific controls.

Automation rules are distinct from the agent's core logic. The agent's core logic — parse invoice, run 3-way match, route approval — is fixed and not configurable. Automation rules are customer-configured policies that apply on top of core logic. They are set in Settings > Automation Rules. Available on Professional and Enterprise plans.

| Rule trigger | Example actions |
|--------------|-----------------|
| Invoice amount above threshold | 'If invoice value exceeds £50,000, flag for CFO review regardless of match result.' Overrides the standard approval routing for high-value invoices. The agent still runs the 3-way match — it just routes differently. |
| Vendor bank details changed | 'If vendor IBAN changes, pause all payment scheduling for that vendor and notify the AP Manager.' Fraud prevention rule. The agent cannot schedule payment to a new IBAN without explicit AP Manager confirmation. |
| No PO found | 'If invoice arrives with no matching PO, notify the procurement team immediately and cc the budget holder for the vendor's category.' Routes the no-PO exception to the right team rather than leaving it in the AP queue. |
| First invoice from new vendor | 'If this is the vendor's first invoice, require Controller approval regardless of amount.' Adds an extra approval step for new vendor relationships. |
| Invoice frequency anomaly | 'If a vendor submits more than 3 invoices in 7 days, flag all of them for AP Manager review.' Detects unusual billing patterns that may indicate a billing error or a compromised vendor account. |
| Specific vendor flag | 'Always flag invoices from [Vendor X] for CFO review.' For vendors under contract renegotiation, under dispute, or with a history of billing errors. |
| Stage timeout | 'If an invoice has been in Exception stage for more than 48 hours, escalate to Controller.' Ensures exceptions are never silently abandoned. The agent manages the escalation automatically. |

Automation rules are configured in plain language, not code. The AP Manager describes the policy; the rule is created from that description. This mirrors Streak's AI Pipeline Creator — the customer describes what they need, the system builds it. Data validation rules (Enterprise only) operate at the field level: they block a Box from moving to a new stage unless required fields are populated and the agent's column values meet defined criteria. For example, a Box cannot move to Approved unless Match Status equals Passed and PO Reference is not empty. The agent enforces these automatically — no human checklist required.

---

## 17. Roles and Permissions

Finance teams are hierarchical by function and by risk. An AP Clerk, an AP Manager, a Financial Controller, and a CFO have fundamentally different access requirements, different decision authorities, and different exposure to sensitive financial data. Clearledgr's permission model must reflect those differences precisely.

Permissions in Clearledgr are role-based, not individual. A role is assigned to a person. The role determines which pipelines they can see, which agent actions they can override, which settings they can modify, and which approval thresholds apply to them.

### Role Definitions

| Role | What they see | What they can do |
|------|---------------|------------------|
| AP Clerk | AP Invoices pipeline and Vendor Onboarding pipeline. Thread sidebar on any invoice email. Agent Activity feed filtered to their own actions. | Process invoices within auto-approve threshold. Flag exceptions. Add comments to Box timelines. Cannot modify settings, cannot approve above their threshold, cannot view ERP write history. |
| AP Manager | All AP Clerk visibility plus full Agent Activity feed, all Saved Views, and approval queue. | Approve invoices up to their threshold. Override agent exception flags with a written reason. Manage vendor onboarding. Modify AP policy settings. Cannot modify ERP connection or autonomy tier configuration. |
| Financial Controller | All AP Manager visibility plus reconciliation pipeline and month-end close pipeline when available. | Approve invoices above AP Manager threshold. Modify all AP policy settings. Accept or decline autonomy tier expansion recommendations. Cannot connect or disconnect ERP. |
| CFO | Full visibility across all pipelines, all Saved Views, all agent activity, and all settings. | Approve any invoice regardless of value. Connect and disconnect ERP. Modify all settings including autonomy tier. Override any agent action within the override window. |
| Read Only | AP Invoices pipeline and Agent Activity feed in read-only mode. No thread sidebar actions. | View pipeline status and agent actions. Cannot approve, flag, override, or modify any setting. For external auditors or board observers. |

### Permission Design Rules

- Permissions are additive upward — every role inherits the full access of the role below it plus additional capabilities. There are no permission gaps between adjacent roles.
- Role assignment is visible to the CFO and Controller at all times. There is no hidden privilege escalation.
- The agent always operates within the permission model. It will not route an approval to someone who lacks authority to grant it. It will not post to the ERP using credentials that exceed the granted write scope.
- A person's role cannot be elevated by a peer. Only a CFO or Controller can promote an AP Manager to Controller access.
- Read Only access is designed for external parties — auditors, board observers, investors. It requires explicit CFO approval to grant and expires after a configurable period.

---

## 18. Error States

Error states in Clearledgr are not edge cases. They are predictable failure modes that occur in the normal operation of a finance product. An ERP that goes offline during a payment run. An invoice PDF that cannot be parsed. A vendor email with no attachment. A PO that exists in the ERP but has already been fully receipted. Each of these requires a specific, designed response from the agent — not a generic error message.

The principle governing all error states: the agent must tell the finance team exactly what happened, exactly why it stopped, and exactly what is needed to resolve it. Vague errors create more work than they prevent.

### ERP Connectivity Errors

| Error | Agent behaviour |
|-------|-----------------|
| ERP unreachable | Agent queues all pending actions locally. Posts to Slack: 'NetSuite connection lost at 14:32. 3 invoices are queued and will process automatically when connection restores. No payments have been delayed.' Retries every 5 minutes. Alerts AP Manager if outage exceeds 30 minutes. |
| Insufficient ERP permissions | Agent stops the specific action that requires the missing permission. Posts to the Box timeline with the exact permission name and a link to where to grant it in the ERP. Does not block other actions that do not require that permission. |
| PO not found in ERP | Agent raises an exception with the specific PO number from the invoice. Posts to the thread sidebar: 'PO-2041 referenced on this invoice does not exist in NetSuite. This may be a typo, an unapproved PO, or a PO from a different entity.' Routes to AP Manager for resolution. |
| GRN not matched | Agent raises a partial match exception. Shows what was found and what was expected. Does not block the invoice from being approved manually if the AP Manager chooses to override. |

### Invoice Processing Errors

| Error | Agent behaviour |
|-------|-----------------|
| Unreadable PDF attachment | Agent flags the email with a red label: 'Invoice attachment could not be parsed.' Posts to sidebar: 'The PDF from Stripe Inc. appears to be scanned or image-based. Please forward a machine-readable version or enter the invoice details manually.' Does not attempt to guess values. |
| No invoice attachment found | Agent scans the email body for invoice data. If none found, flags the thread: 'No invoice attachment detected. If this email contains a verbal payment request, it must be processed manually.' Does not create a Box automatically. |
| Duplicate invoice detected | Agent pauses processing and posts to the sidebar: 'INV-2841 from Stripe Inc. for £12,400 matches INV-2801 paid on 14 March. This may be a duplicate. Confirm this is a new invoice before approving.' Requires explicit AP Manager confirmation to proceed. |
| Amount extraction conflict | Invoice shows different amounts in different locations (header vs line item total). Agent surfaces both values and requests clarification: 'Header shows £8,922. Line item total shows £8,500. Which value should be used for matching?' Does not guess. |

### Vendor and Onboarding Errors

| Error | Agent behaviour |
|-------|-----------------|
| Vendor not in master | Agent cannot process the invoice. Posts to sidebar: 'Stripe Inc. is not in your vendor master. Initiate vendor onboarding to activate this vendor before processing their invoices.' Provides one-click link to start the onboarding flow. |
| IBAN validation failure | Micro-deposit fails due to invalid IBAN format or rejected by receiving bank. Agent posts to vendor onboarding Box: 'Micro-deposit to GB82 WEST 1234 5698 7654 32 was rejected by Barclays. The IBAN may be incorrect. Agent has sent the vendor a request to resubmit their bank details.' |
| KYC document rejected | Agent identifies a document that does not meet requirements (expired ID, wrong document type). Posts to vendor onboarding Box with the specific issue: 'Director ID submitted by Stripe Inc. expired on 12 Jan 2024. A valid ID is required. Agent has notified the vendor contact.' |
| Vendor unresponsive | After three chase attempts over 72 hours, agent escalates to AP Manager via Slack: 'Brex Inc. has not responded to onboarding after 3 contacts over 72h. Manual outreach may be required. The vendor contact on file is ops@brex.com.' |

### Error State Design Rules

- Every error message names the specific entity involved. 'PO-2041' not 'the purchase order'. 'Stripe Inc.' not 'the vendor'. 'NetSuite' not 'the ERP'.
- Every error message states what the agent has already done, what it has not done, and what it needs. Never leave the finance team uncertain about the current state.
- Errors are surfaced in the same place the work happens — in the thread sidebar for invoice errors, in the onboarding Box timeline for vendor errors, in Slack for system errors. Finance teams do not check a separate error log.
- The agent never silently skips an action due to an error. If it stops processing, it always says so.

---

## 19. Security and Data Handling

Security is not a section of the product. It is a property of every decision made about data, access, and agent behaviour. For enterprise finance teams, security and data handling are procurement requirements — they are evaluated before the product is trialled, not after it is adopted.

Clearledgr's security posture is governed by one principle: the product processes sensitive financial data on behalf of its customers. It is not the owner of that data. It is a steward. Every design and engineering decision must reflect that distinction.

### What Clearledgr Stores

| Data type | Storage and retention policy |
|-----------|------------------------------|
| Invoice metadata | Invoice reference, vendor name, amount, due date, match status, and agent column values are stored in the Clearledgr Box database to power the pipeline view and thread sidebar. This metadata is encrypted at rest. It is never sold, never used for model training, never shared with third parties. |
| Invoice attachments | PDF and structured invoice files are processed in memory during extraction and immediately discarded. Clearledgr does not store invoice PDFs. The original attachment remains in Gmail, which is the customer's data environment. What Clearledgr does retain is a replay record per invoice: the extracted JSON fields, and an anonymised version of the raw OCR text where vendor-identifying strings are replaced with category tokens. This replay record enables the model improvement loop and historical replay testing (§7.7, §7.9) without storing the original document. Enterprise security reviews should note this distinction: Clearledgr retains machine-readable structured output and anonymised text. It does not retain the PDF itself, its embedded metadata, or unredacted vendor content. |
| Box timelines | Every agent action log entry is stored for the duration defined in Settings (default: 7 years). Timeline data is encrypted at rest and in transit. It is the audit trail and is treated with the same controls as financial records. |
| ERP credentials | OAuth tokens for ERP connections are stored encrypted using customer-specific keys. Clearledgr never stores ERP passwords. Tokens can be revoked by the customer at any time from ERP Settings, immediately terminating agent access. |
| Email content | Clearledgr reads email content to extract invoice data. It does not store email bodies or attachments beyond the processing window. It does not read emails unrelated to finance workflows. |

### What Clearledgr Does Not Store

- Full invoice PDFs or attachments of any kind after extraction is complete.
- Email bodies from threads that do not contain finance-relevant content.
- Employee personal data beyond what is necessary for approval routing (name, email, role).
- Bank account numbers or IBANs in plaintext at any point. IBANs are stored in tokenised form and displayed masked in the UI (`GB82 **** **** **** 4332`).
- ERP financial data beyond what is required for matching and posting. Clearledgr reads specific records for specific invoices — it does not cache or replicate the ERP.

### Access and Authentication

- Clearledgr authenticates via Google OAuth. There are no Clearledgr passwords. Access is tied to the user's Google Workspace identity.
- All data in transit is encrypted using TLS 1.3. There are no unencrypted data paths between the extension, the Clearledgr backend, and the ERP.
- The extension operates within Gmail's content security policy. It cannot read data from other browser tabs or other Google Workspace applications.
- Multi-factor authentication is inherited from the customer's Google Workspace MFA configuration. Clearledgr does not bypass or weaken existing MFA.

### Compliance Posture

| Standard | Clearledgr's posture |
|----------|----------------------|
| GDPR | Clearledgr is a data processor. The customer is the data controller. A Data Processing Agreement is available as standard and is required before any enterprise customer goes live. |
| UK GDPR | Clearledgr Ltd is registered in the UK. Processing of personal data for UK customers occurs in UK or EU infrastructure. Right to erasure requests are fulfilled within 30 days. |
| SOC 2 Type II | Target certification for Series A. Security controls are designed to meet SOC 2 criteria from the first line of code. Evidence collection begins at launch. |
| Financial record retention | Agent action logs are retained for a minimum of 7 years by default, consistent with UK Companies Act requirements. Customers may extend this period but not shorten it below the statutory minimum. |
| Audit access | The Read Only role provides external auditors with time-limited, scoped access to Box timelines and pipeline views. No data export to unsecured formats. All auditor access is logged. |

> *The correct framing for enterprise procurement conversations: Clearledgr does not hold your financial data. Your data lives in your ERP and your Gmail, both of which you already trust. Clearledgr is the intelligence layer that connects them — processing data in transit, not at rest.*

---

*This document is a living reference. The design principles (§4), the agent communication model and trust arc (§7), the fraud controls (§8), and the LLM guardrail architecture (§7.6) are foundational — they should not be revised without explicit product-level discussion and a written record of the reasoning. The competitive landscape (§12), subscription model (§13), and expansion platform (§3) are aspirational and should be updated as the market and product evolve. The onboarding section (§15) should be updated as ERP connector coverage expands. The Backoffice section (§14) is a summary — the full operational detail lives in the internal operations document.*
