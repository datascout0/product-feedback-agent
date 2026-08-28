# Decision Log

## D001 — Product scope

**Decision:** Build Product Feedback Agent as a 3-day solo MVP with an optional Day 4.

**Rationale:** Prioritise one credible end-to-end product journey over feature breadth.

---

## D002 — Human decision boundary

**Decision:** Product Feedback Agent recommends evidence-backed product opportunities but does not autonomously decide the roadmap.

**Rationale:** Opportunity scores are decision-support signals rather than automated prioritisation decisions.

---

## D003 — Evidence-Backed Opportunity Tree

**Decision:** Represent the analysis conceptually as:

Product Objective
→ Opportunities
→ Underlying Problems / Needs
→ Customer Evidence
→ Evidence Gaps / Discovery Questions

**V1 boundary:** Do not build a complete autonomous Opportunity Solution Tree. Product Feedback Agent will not automatically choose solutions, experiments, or roadmap items.

**Rationale:** This strengthens the product-discovery model without materially expanding the MVP.

---

## D004 — Agent architecture

**Decision:** Use one bounded analyst → deterministic validator → critic → optional single revision workflow.

**Rationale:** Keep reasoning inspectable, rate-limit-aware, measurable, and governed.
