# Product Feedback Agent

Product Feedback Agent is an agentic product-feedback workflow that transforms fragmented customer evidence and optional product context into critic-reviewed, evidence-backed product opportunities.

## Product Principle

Product Feedback Agent recommends opportunities worth investigating. It does not autonomously decide the roadmap.

## V1 Journey

Feedback
→ Normalisation
→ Evidence retrieval
→ Opportunity analysis
→ Evidence-backed opportunity tree
→ Deterministic validation and scoring
→ Critic review
→ Human approval
→ Governed GitHub action

## Status

3-day solo MVP with optional Day 4.

## Planned Stack

- Python 3.12
- Local embeddings
- Vector retrieval
- MCP
- Gemini
- n8n
- GitHub REST API
- pytest

## V1 Output

Three evidence-backed product opportunity cards grouped beneath the user-supplied product objective, with citations, transparent scoring, evidence gaps, discovery questions, and human review.

## Safety Boundary

Customer feedback is treated as untrusted data, never as agent instruction. External actions require explicit human approval.
