# A Memo from Mo

*Clearledgr · April 2026*

---

## The short version

Clearledgr is the stateful coordination layer for finance operations. We started with accounts payable because it is the highest-volume, most painful coordination problem in the finance function. We will expand across every finance workflow because every finance workflow has the same shape — long-running, multi-participant, exception-prone, and held together by human memory because no system holds it.

This memo exists to state, plainly, what we are building, why we believe it is worth building, how we intend to operate while we build it, and what we will not compromise on to get there. It is the reference I want every person who joins this company to read before their first day. It is the reference I expect to be held to by our investors, our customers, and the people I work with every day.

## What we believe

Finance operations is defined by coordination. Every finance workflow — invoice processing, vendor onboarding, receivables, reconciliation, close, expense management, treasury, audit — is a long-running process with state that today lives in nobody's system and in everybody's head.

The ERP holds results, not workflows. Email holds communication fragments. Spreadsheets hold the tracker someone built last quarter. The workflow itself — what's in flight, what's blocked, what's next, what's exceptional and why — lives in the mental memory of the finance team. AP clerks remember which invoices are waiting on which GRN. Controllers remember which close tasks are blocked by which reconciliation. CFOs remember the three things they are waiting on. This is not a minor gap. It is the defining structural problem of the finance function, and the cost of it is enormous — distributed across headcount, close cycles, audit preparation, error rates, and lost strategic capacity.

The problem has existed for decades. It has not been solved for two reasons. First, no system in the finance stack was designed to hold workflow state — ERPs are systems of record, email is a communication protocol, portals are data-collection interfaces. Retrofitting any of them for workflow state has never worked. Second, the specialist tools that do hold state for one workflow — BlackLine for close, Stampli for AP approval, HighRadius for AR — do not generalise. Each exists because one workflow became painful enough to support a standalone product. But the state models do not talk to each other, and the finance team is still the integration layer across them.

What has changed is that the agent piece — the mechanism for advancing workflow state autonomously rather than just tracking it — is now technically tractable. This is the bet the company is making. Not that finance teams need a coordination layer; that is established. Not that agents can summarise and draft; that is established. The bet is that **agents constrained by the right architecture can be trusted to advance finance workflows autonomously**, and that the product that delivers this trust first becomes the coordination infrastructure for the finance function.

## What we are building

Clearledgr is the stateful coordination layer for finance operations. Every workflow instance gets a Box — a persistent, attributable, queryable home for its state, its timeline, its exceptions, and its outcome. An agent advances each workflow autonomously where it can. Humans decide on the exceptions the agent surfaces. The finance team interacts with the workflow through the tools they already use — Gmail, Slack, and their ERP — but the coordination itself lives in Clearledgr.

The wedge is accounts payable. Invoice ingestion, data extraction, three-way matching, exception flagging, approval routing, ERP posting, payment scheduling — all handled inside the Box, with the agent acting and the finance team approving exceptions. Alongside AP we ship vendor onboarding because you cannot pay a vendor who is not in your system. In V1.2 we add commission clawback for our design partner Booking.com — a workflow with nothing to do with AP invoicing but exactly the same shape, which is the cleanest demonstration of the platform thesis.

After that, the roadmap is structural, not aspirational. Every finance workflow is Box-shaped. Each new workflow earns the right to the next through demonstrated performance. By the time we reach close and treasury, we are not competing with point solutions on feature depth — we are the coordination infrastructure across workflows, and the compounding has done its work.

## How we operate

A few principles govern how I want us to work. These are not aspirations. They are constraints. When a decision conflicts with a principle, the principle wins.

**Rules decide, LLM describes.** Our agent is not at the mercy of model judgment. Every financial write is constrained by deterministic rules. The LLM classifies and summarises; the deterministic layer calculates, validates, and posts. This is the architectural discipline that makes the bet winnable. We build every agent pipeline this way, without exception.

**Trust is earned, not claimed.** New customers onboard with the agent in a supervised tier. Autonomy expands only after performance is demonstrated. This is how we build a product finance teams will trust with their ERP, their vendor master, and eventually their close. We do not market autonomy we have not earned; we earn it, then we offer it.

**Every agent action is explainable.** The agent never acts silently. Every action is logged with what it did, why it did it, and what happens next. Finance teams will not grant autonomous action to a system they cannot audit. Explainability is a prerequisite, not a feature.

**Performance is a design constraint, not an afterthought.** A Gmail sidebar that takes five seconds to load is uninstalled. A Kanban that freezes on scroll is abandoned. Every surface has a performance budget that must be met before it ships. These are pass/fail criteria, not aspirations.

**The product is invisible on the happy path.** Finance teams should not have to check the pipeline every morning to see what the agent did. They should see the output where they already are — an inbox label, a Slack message, a Box card — and nothing else. The people whose workflow is being advanced should feel their work getting lighter without feeling like they are using a new tool. The interface exists for exceptions, not for the happy path. If people describe their experience as "I barely notice it, and everything gets handled," the product is working.

**We are honest about what is hard.** The central technical bet of the company — that agents can be trusted to advance finance workflows autonomously — is not won. It is winnable, and we are architected to win it, but we do not pretend it is easier than it is. With customers, with investors, with each other — we do not oversell our current state. We sell our trajectory, and we deliver.

## Who we serve

We serve finance teams at mid-market and enterprise scale. Teams with invoice volume, approval hierarchies, ERP systems, audit requirements, and the operational weight that makes coordination infrastructure genuinely valuable rather than nice-to-have. We do not target SMBs first; the coordination problem exists at that scale too, but the buyer cost and the onboarding complexity do not support a credible wedge there.

Within those teams, we design for the Controller and CFO — the people who make the buying decision and the renewal decision — while obsessing over the daily experience of whoever owns the workflow on the ground. For AP, that is the AP Clerk and AP Manager. For close, it will be the Controller. For treasury, the CFO's direct team. Adoption is won or lost at the ground level. The buying decision is made above it. Both audiences matter; neither is the whole picture.

Our first enterprise design partner is Booking.com. Our initial commercial focus spans Europe, the UK, and Africa, expanding into the US, Canada, LATAM, and APAC as the product and the team grow. The Africa focus is not incidental — it reflects the distribution depth the founding team has in that market and the scale at which finance operations pain compounds in regions where ERP coverage is thinner and coordination cost is higher. Our target ERPs in V1 are SAP, NetSuite, Xero, and QuickBooks. We do not support Outlook in V1; this is a stated boundary, not an oversight, and we say so before procurement asks.

## What we will not compromise on

Some commitments protect the product and the company from themselves. These are the ones I will not negotiate away under commercial pressure.

We will not ship a write path that bypasses the deterministic guardrails. No financial posting happens at the mercy of model judgment, ever, under any timeline pressure.

We will not claim autonomous performance we have not earned with a specific customer. Every expansion of autonomy is tied to a performance record. No customer is given more automation than their data supports, and no sales conversation implies otherwise.

We will not become a system of record for financial data. Your financial data lives in your ERP. We hold the workflow around it, not the data itself. This is both a trust commitment and an architectural discipline — we do not migrate you to us, and we do not ask you to migrate your data.

We will not let performance degrade silently. If a surface starts failing its performance budget, it is treated as a P0. If the agent's override rate spikes, we page the on-call engineer. We do not ship fixes; we ship reliability.

We will not underinvest in the vendor-facing experience. When a new vendor is onboarded, their interaction with Clearledgr's portal is the only thing they will ever see of our product. That portal reflects on our customer's reputation, not ours. We build it accordingly.

## Who we are

I am Mo Mbalam. I spent three years as an accounting associate at the University of Ghana before moving into fintech, where I led go-to-market for Paystack in Ghana and led business development for Anchor, a YC-backed banking-as-a-service company. I founded two earlier ventures in Ghana before Clearledgr. I relocated to the Netherlands in 2024. I have spent most of my career close to the distribution layer of African and European fintech, which is where the instinct for this product came from.

I am building Clearledgr with Joseph Isiramen, our CRO, who spent his formative years in enterprise sales at Datadog and brings the go-to-market depth this category demands. And with Suleiman Mohammed, our CTO, a repeat founder with the agent architecture and engineering judgment to deliver on the technical bet the company is making. We believe we are the right team for this problem: distribution credibility in the fintech surface we grew up in, enterprise sales experience for the mid-market and enterprise buyer, and the technical ambition to build the coordination infrastructure we believe the finance function has been waiting for.

The team will grow. The memo will age. But the discipline will not.

## What this is and is not

This memo is the reference document for what Clearledgr is at its core. It is not the pitch deck. It is not the investor memo. It is not the product spec. It is the commitment the company makes to itself — the principles that govern decisions when the decisions get hard, and the articulation of what we are trying to build that every other document in the company should reconcile with.

If you are joining Clearledgr, this is what you are joining. If you are investing in Clearledgr, this is what you are backing. If you are buying from Clearledgr, this is what you are buying.

The thesis is specific. The commitment is specific. The bet is specific. The work is the work.

— Mo
