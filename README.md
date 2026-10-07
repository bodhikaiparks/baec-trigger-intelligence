# BAEC Trigger Intelligence

**BAEC Engine 1: Capture + Verification**

Version 1.0

**Live demo:** https://baec-engine1.streamlit.app

Research prototype for preserving buyer-articulated conditions that may make future evaluation worthwhile.

**Remember what the buyer said mattered, then watch for it.**

## What this project is

BAEC Trigger Intelligence is a research and software project built around the concept of a Buyer-Articulated Evaluation Contingency, or BAEC.

A BAEC is an explicit buyer-generated statement made while the buyer is not currently evaluating relevant alternatives that identifies a prospective condition the buyer expects would make initiating or reopening evaluation worthwhile.

BAEC Engine 1 focuses on Capture + Verification.

The public prototype demonstrates a controlled path from buyer interaction evidence to AI-assisted extraction, human verification, deterministic classification and explicit authorization.

The public browser demonstration intentionally stops before the separate confirmation executor.

## Public demo

The demonstration uses a fixed synthetic Harbor interaction and a prerecorded AI artifact.

No live AI provider call is made during the demo.

AI output remains inference until reviewed by a human.

The demo does not predict purchase intent and does not claim that a buyer is currently evaluating alternatives.

A confirmed BAEC is not automatically an Active Opportunity.

## Research basis

BAEC Engine 1 is derived from the unpublished conceptual working paper:

*When the Buyer Is Not In-Market: Buyer-Articulated Evaluation Contingencies in B2B Sales Encounters*

The full manuscript is intentionally not included in this public repository.

The accompanying Research and Technical Brief documents the theoretical basis, Engine 1 implementation, limitations and reproducibility information relevant to the public research prototype.

If you are interested in reviewing the manuscript or providing feedback on the BAEC framework, please contact the author directly.

## Research and Technical Brief

The public Research and Technical Brief is packaged with the demo at:

`public_demo_assets/BAEC_Engine_1_Research_and_Technical_Brief.pdf`

It is the Version 1.0 Brief (7 October 2026) and records the release facts, including the live-QA software baseline and the software verification result.

## Run locally

Install the public runtime dependencies:

```
python -m pip install -r requirements.txt
```

Run:

```
streamlit run streamlit_app.py
```

No Anthropic API key or other AI-provider credential is required for the public demo.

## Important limitations

The BAEC framework has not been empirically validated.

The software prototype is not evidence that BAEC improves conversion, revenue, forecasting accuracy or buyer outcomes.

The system does not predict who will buy.

A detected signal or AI suggestion does not establish that a buyer is evaluating or intends to purchase.

## Project status

BAEC Engine 1: Capture + Verification, Version 1.0

Public research prototype

Engine 2: Monitoring + Correspondence is future product-roadmap work and is not part of this release.
