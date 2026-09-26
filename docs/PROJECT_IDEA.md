# Engineering Intelligence for GitHub

*A tool that connects to a team's GitHub repository and tells them what's off track, why it happened, and what to do about it.*

---

## 1. The Problem

Engineering teams generate an enormous amount of signal on GitHub — pull requests, reviews, commits, issues, milestones — but almost none of it is *interpreted*. A tech lead can see that there are 18 open PRs and 42 open issues, but GitHub will never tell them:

- Delivery has slowed down by 88% this week.
- The cause is review delays, not slow coding.
- The delays trace back to one reviewer holding 71% of pending reviews.
- Here are the three things to change on Monday.

Existing tools land on one of two extremes. Dashboards (LinearB, Swarmia, Waydev) show charts and leave the interpretation to you. Generic "AI for GitHub" tools dump raw repository data into an LLM and hope for insight — which produces confident, unverifiable narration.

**The gap: nobody does the causal reasoning in between.** That's the product.

---

## 2. The Core Idea

The system has **three separate brains**, and keeping them separate is the entire design thesis.

| Layer | Question it answers | How it works |
|---|---|---|
| **Analytics Engine** | *What happened?* | SQL + statistics over synced GitHub data |
| **Detection & Recommendation Engine** | *What's wrong, and what should we do?* | Deterministic Python rules, thresholds, decomposition |
| **AI / LLM Layer** | *Explain it in plain English.* | Receives structured findings, never raw GitHub data |

The critical constraint: **the LLM never does the analysis.** It receives a finished, evidence-backed JSON finding and turns it into readable language. Every number on screen is reproducible from the database — no hallucinated metrics, no unfalsifiable claims.

This is what separates the project from "I sent GitHub data to ChatGPT."

---

## 3. Walkthrough: How It Actually Works

Take a real scenario. `acme-commerce/backend` — 6 developers, 18 open PRs, 42 open issues, a milestone due in 5 days.

**Step 1 — Connect.** The engineering lead authenticates with GitHub OAuth and selects the repository. The backend now has read access to PRs, issues, reviews, commits, and milestones.

**Step 2 — Sync.** A Python collector pulls historical data (and then keeps up via webhooks) into PostgreSQL. For each PR it records the timeline that matters: created → first review → approved → merged.

**Step 3 — Measure.** The analytics engine computes PR cycle time and compares the current week against a 30-day baseline:

```
Baseline (30d):  18 hours
Current week:    34 hours
Change:          +88%
```

**Step 4 — Decompose.** Instead of stopping at "cycle time is up," the system splits the metric into its constituent phases:

```
Coding time         12h → 14h      +17%
Review waiting      6h  → 20h     +233%   ← the real problem
Merge waiting       0.5h → 1h     +100%
```

The slowdown isn't developers coding slowly. It's PRs sitting idle waiting for a human.

**Step 5 — Investigate.** The detection engine drills into reviewer activity:

```
Rahul    15 reviews
Priya     3 reviews
Amit      2 reviews

7 PRs currently awaiting review
5 of those 7 assigned to Rahul
```

It emits a structured finding:

```json
{
  "type": "REVIEW_BOTTLENECK",
  "severity": "HIGH",
  "confidence": 0.91,
  "evidence": [
    "Review wait time increased 233%",
    "7 PRs are waiting for review",
    "71% of pending reviews are assigned to one reviewer"
  ]
}
```

**No LLM was involved.** This is Python, SQL, and statistics.

**Step 6 — Score.** A weighted health model updates:

```
                Before        After
Delivery          85       →    61  🔴
Reviews           82       →    42  🔴
Issues            78       →    76  🟢
PR Quality        84       →    83  🟢
Activity          80       →    79  🟢
─────────────────────────────────────
Overall           82  🟢   →    67  🟡  At Risk
```

**Step 7 — Recommend.** A deterministic rule maps `REVIEW_BOTTLENECK` to concrete actions: redistribute pending reviews, prioritize PRs #144 and #147, add a second reviewer for the payment-service area.

At this point the product is already useful with zero AI.

**Step 8 — Narrate.** *Now* the LLM enters, receiving only the structured findings, and produces:

> **🔴 Project at risk: Review bottleneck**
>
> The biggest delivery problem this week is PR review time. Average review waiting time has increased by 233%, and 7 PRs are currently waiting for review. Most of the pending review workload is concentrated on one reviewer, which is likely contributing to the delay.

**Step 9 — Ask.** The dashboard shows the health score with `[Why?]` and `[What should I do?]` buttons. The lead can also type free-form questions — *"What should we fix first?"* — and the LLM answers by calling backend tools (`get_project_risks()`, `get_review_bottlenecks()`) rather than by guessing.

---

## 4. Architecture

```
                    ┌──────────────┐
                    │    GitHub    │
                    └──────┬───────┘
                           │  REST API + Webhooks
                           ↓
                 ┌──────────────────────┐
                 │   Data Collector     │   Python
                 └──────────┬───────────┘
                            ↓
                 ┌──────────────────────┐
                 │     PostgreSQL       │
                 └──────────┬───────────┘
                            ↓
                 ┌──────────────────────┐
                 │   Metrics Engine     │   PR time, review time,
                 │                      │   issue age, PR size
                 └──────────┬───────────┘
                            ↓
                 ┌──────────────────────┐
                 │  Detection Engine    │   bottlenecks, risks,
                 │                      │   anomalies
                 └──────────┬───────────┘
                            ↓
                 ┌──────────────────────┐
                 │ Recommendation Engine│
                 └──────────┬───────────┘
                            │
              ┌─────────────┴─────────────┐
              ↓                           ↓
      React Dashboard              LLM (explain / Q&A)
```

### Where MCP fits

MCP (Model Context Protocol) sits **between an AI client and this application's capabilities** — it is not something the LLM contains:

```
User → AI Client / Agent → MCP → project-intelligence tools
                                        ↓
                                  FastAPI services
                                        ↓
                            PostgreSQL / Analytics / GitHub
```

Exposed tools: `get_project_health()`, `get_project_metrics()`, `get_project_risks()`, `get_open_prs()`, `get_stale_issues()`, `get_review_bottlenecks()`, `get_recommendations()`.

The payoff: because the analysis lives behind MCP tools, the same intelligence is reachable from Claude Desktop, an IDE, or a Slack bot — not just from this app's own dashboard.

**Stack:** Python / FastAPI · PostgreSQL · React · GitHub REST API + webhooks · an LLM API · MCP server.

---

## 5. Scope

### In scope
- Single-repository analysis, extended to multi-repository later
- PR lifecycle, review behaviour, issue backlog health, milestone risk
- Deterministic detection with explicit evidence and confidence scores
- A composite health score with per-dimension breakdown
- Natural-language explanation and Q&A grounded strictly in computed findings
- MCP server exposing the analysis to external AI clients

### Explicitly out of scope
- Individual developer performance ranking or surveillance. Findings describe *system* bottlenecks, not underperforming people — this is a deliberate product stance, not an omission.
- Code quality / static analysis (that's SonarQube's job)
- Project management writes back into GitHub (no auto-assigning, no auto-closing)
- Jira / Linear / Slack ingestion — GitHub only, at least initially
- Any LLM-generated metric. If a number appears in the UI, it came from SQL.

---

## 6. MVP Roadmap

### Initial MVP — "It works and it's honest"

The goal is a system that produces one genuinely correct, evidence-backed finding end to end.

1. **GitHub OAuth + repository selection** — connect one repo.
2. **Data collector** — historical backfill of PRs, reviews, issues, commits, milestones into PostgreSQL. Polling is fine here; webhooks can wait.
3. **Metrics engine** — PR cycle time (decomposed into coding / review-wait / merge-wait), review turnaround, issue age, PR size.
4. **Detection engine** — 3–5 deterministic rules: review bottleneck, stale PRs, issue backlog growth, oversized PRs, milestone-at-risk.
5. **Health score** — 5 weighted dimensions producing a 0–100 overall score with history.
6. **Recommendation engine** — a rule-to-actions mapping, fully deterministic.
7. **React dashboard** — health score, trend line, findings list with expandable evidence.

**Deliberately no AI in the initial MVP.** If the product is useful without the LLM, the LLM becomes a genuine enhancement rather than a disguise for missing logic.

### Advanced MVP — "It explains itself and plugs into anything"

8. **LLM explanation layer** — structured findings in, weekly narrative summary out. Strict grounding: the prompt contains only computed findings.
9. **Conversational Q&A** — "Why is our project at risk?", "What should we fix first?" — answered via tool calls into the backend, with citations back to the underlying metrics.
10. **MCP server** — expose the seven tools so Claude Desktop, IDEs, or agents can query the analysis directly.
11. **Webhook-driven real-time sync** — replace polling; findings update as events land.
12. **Multi-repository and team-level rollups** — org-wide health, cross-repo comparison.
13. **Trend and anomaly detection** — statistical baselines per repository instead of fixed thresholds, so a fast-moving repo and a slow one are each judged against themselves.
14. **Alerting** — Slack or email when health crosses a threshold or a HIGH-severity finding appears.
15. **Predictive milestone risk** — "at the current merge rate, this milestone completes 4 days late."

---

## 7. Why This Is a Strong Project

- **It solves a real problem.** Every engineering team above ~5 people hits review bottlenecks and blames the wrong thing.
- **It demonstrates system design, not API plumbing.** Data pipeline → storage → analytics → detection → presentation is a genuine multi-layer architecture.
- **The AI usage is defensible.** In an interview, "the LLM explains deterministic findings and never computes metrics" is a far better answer than "I send GitHub data to an LLM." It shows judgement about *where* AI belongs.
- **MCP is real, current, and rarely seen in portfolios** — and here it's used for its actual purpose (standardized tool access) rather than as a buzzword.
- **It demos in 60 seconds.** Connect a repo → see a red health score → click *Why?* → get an explanation. That's a memorable interview moment.
