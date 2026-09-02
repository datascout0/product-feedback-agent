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

---

## D005 — Runtime model and Gemini quota

**Runtime model:** `gemini-3.7-flash`

**Active project limits recorded 2026-09-02:**
- RPM: 5
- TPM: 250,000 input tokens
- RPD: 20

**Workflow budget:**
- Passing run: 2 Gemini requests maximum
- Revision run: 3 Gemini requests maximum
- Minimum sequential request spacing: 14 seconds

**Development rule:** Reuse cached evidence packets and saved model responses when debugging non-LLM parts of the workflow.

**Rationale:** The project-specific RPD limit is restrictive, so model requests must be reserved for analyst/critic behaviour rather than routine development testing.
