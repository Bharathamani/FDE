# FDE Take-Home Case Study: Cold-Chain Alerting Engine

## Before you start

This exercise mirrors a real Forward Deployed Engineer (FDE) engagement: a customer brings you a genuine but partially-specified problem, and you're expected to (1) clarify/assume what's needed, (2) propose a solution, (3) build a working version of it, and (4) present and defend your approach to the customer (in this case, our panel).

**What we're evaluating:** how you handle ambiguity, the soundness of your design judgment, your ability to build a working solution under real constraints, how clearly you communicate your reasoning, and how you respond when your approach is challenged. We are **not** grading for a "perfect" or exhaustive solution — a well-reasoned, appropriately-scoped solution with clearly stated trade-offs is exactly what we want to see.

## The customer

**Nimbus Freight** operates a fleet of refrigerated ("reefer") trucks carrying temperature-sensitive cargo for pharmaceutical and grocery customers. Their VP of Operations, Priya Shah, reached out with the following ask:

> "Every truck has a sensor reporting temperature every few minutes. If a truck's cargo gets too warm for too long, we need to know — but right now our ops team either gets buried in alerts for every single reading above threshold (so they've started ignoring them), or they miss a real problem because it got lost in the noise.
>
> We want something that looks at the incoming sensor data and only alerts us when there's a genuinely sustained issue with a specific truck — not every brief blip, and not the same ongoing issue re-alerting over and over every few minutes. What would you build for us?"

**Note:** Priya's ask is intentionally incomplete. You will need to decide things like: what temperature counts as "too warm," how long is "too long" before it's worth alerting, and how you'd want the system to behave if sensor data arrives with gaps or out of order (which does happen in the real world with truck connectivity). **Document whatever assumptions you make.**

## What we're providing you

- A seed dataset of realistic (synthetic) sensor readings across 5 trucks over about an hour.
- A minimal starter repository scaffold with a basic violation-detection utility, so you're not starting from a completely blank page.
- *(See `starter_kit/` alongside this brief.)*

## What you have full freedom on

- **Language/stack** — use whatever you're most productive in.
- **Scope** — you decide what's in v1 versus "future work." A smaller, well-reasoned scope beats an ambitious, half-finished one.
- **Exact thresholds** — pick reasonable defaults and state your reasoning; there's no single "correct" number we're looking for.

## Time expectation

We expect this to take **roughly 3-4 focused hours**, with a full week of calendar time to fit it in. If you're well past 4-5 hours, **stop** — use your README to describe what you'd tackle next.

## Required deliverables

1. **`SOLUTION_DESIGN.md`** (1-2 pages): the problem restated in your own words, your assumptions, your proposed approach, the trade-offs you considered, and what you'd do with more time/data/budget.
2. **Working implementation** — should run end-to-end against the provided dataset and produce a clear list of "here's what I'd alert on, and why."
3. **`README.md`** — how to run it, known limitations.

## Optional bonus track (not required, never penalized if skipped)

You're welcome to explore an AI-powered enhancement — for example, using an LLM to turn a raw alert (truck ID, thresholds, duration) into a short, human-readable summary an ops person could read at a glance (e.g., "Truck T-04 has been above the safe temperature range for over 20 minutes — likely a reefer unit failure"). This is a **plus signal**, not a gate.

## What happens after you submit

We'll review your submission ahead of a **30-45 minute live session**: a walkthrough of your design doc and a short demo, followed by Q&A, including "what if..." questions and the occasional new constraint introduced mid-conversation.

## Submission

Send us a link to your repository (or a zip archive) containing your code, `SOLUTION_DESIGN.md`, and `README.md`.
