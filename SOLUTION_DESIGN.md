# Cold-Chain Alerting Engine — Solution Design

Companion document: **[DECISIONS.md](DECISIONS.md)** is the decision register for
the threshold, duration, gap, out-of-order, duplicate, and recovery choices. This
document explains the overall design and trade-offs.

## 1. Problem, restated

Nimbus Freight's ops team receives a temperature reading every few minutes from
each reefer truck. The current alerting is per-reading, so it is both noisy and
unsafe: brief spikes create alert fatigue, while a real sustained excursion can
disappear into the same stream. The team has started ignoring alerts. The system
needs to answer a narrower question:

> Is there a genuinely sustained temperature problem on a specific truck, and is
> this the first time we are telling ops about this particular incident?

The output should be an **incident**, not a stream of threshold crossings. It
should open once, carry enough evidence to act on, and resolve once, without
re-alerting while the same issue continues.

## 2. Assumptions I would confirm with Priya

These are explicit defaults, not universal truths. The full decision register,
including alternatives, edge cases, and sign-off fields, is in
[DECISIONS.md](DECISIONS.md):

- **Safe limit:** 8.0 C. This is a common cold-chain ceiling for refrigerated
  pharma/grocery cargo. Real setpoints are cargo- and customer-specific; the
  policy is configurable per truck and designed for per-cargo extension.
- **"Too long":** 20 minutes above the limit. Long enough to ignore a single
  probe spike or door-open blip, short enough to act before most cargo is
  meaningfully exposed. It is a configurable policy, not a hidden constant.
- **Sample cadence:** roughly 5 minutes, per the brief.
- **Timestamps:** ISO-8601, interpreted as UTC when no offset is present. All
  outputs are UTC.
- **One temperature policy for the seed data**, because the dataset has no cargo
  type, customer, or setpoint metadata.
- **A bounded gap is tolerable.** A missing 5-minute reading should not erase an
  otherwise continuous excursion, but an arbitrarily long gap must not be
  treated as proof of continuity.
- **Missing data is not recovery.** If a truck stops reporting, the system should
  not quietly resolve an active incident.

The starter kit itself flags the two data-quality traps: it assumes sorted input,
and it computes duration from reading count rather than elapsed time. The provided
dataset violates both assumptions (T-05 is out of order; T-04 is missing a
reading), so the solution deliberately replaces those helpers.

## 3. Proposed approach

I built a small event-time engine with five parts:

1. **Ingest and normalise.** Readings are validated and upserted by
   `(truck_id, timestamp)`, so sensor retries and replay are idempotent.
2. **Per-truck event-time ordering.** Each truck's readings are sorted and
   duplicate timestamps collapsed before evaluation. This fixes T-05.
3. **Sustained-excursion state machine.** Each truck moves through
   `IDLE -> PENDING -> ACTIVE -> RESOLVED`. `PENDING` means "above threshold but
   not yet proven sustained"; `ACTIVE` means "alert already emitted"; `RESOLVED`
   means "recovery evidence complete". The state machine is pure and has no
   wall-clock dependency.
4. **Incident identity and deduplication.** An incident's ID is a deterministic
   hash of truck + episode start. Alert lifecycle events are stored with a unique
   `(incident_id, event_type)` constraint. Recomputing the truck after a late
   reading updates the same incident; it cannot create a second "opened" event.
5. **Ops surface.** A FastAPI service exposes ingest, alert, truck, and dashboard
   endpoints. A single-page dashboard shows active/resolved incidents, evidence,
   fleet status, the current policy, and lifecycle events. The CLI adds a
   data-quality view and a threshold × duration what-if sweep. Docker packaging
   makes the demo reproducible.

### Alert rule

For a truck with policy `(threshold=8.0 C, clear=7.5 C, duration=20 min)`:

- **Start a candidate** when a reading is `> 8.0 C`.
- **Promote to an alert** only when all of the following hold:
  - elapsed time between the first and latest breach is `>= 20 min`;
  - there are `>= 3` breach readings;
  - breach readings cover `>= 50%` of the expected 5-minute samples; and
  - no gap in the candidate run exceeds `15 min`.
- **Keep the incident open** while readings remain in the hysteresis band
  `(7.5, 8.0]` or remain above threshold.
- **Resolve** after `2` consecutive readings `<= 7.5 C`.
- **Emit exactly one `OPENED` event per incident**, and at most one `RESOLVED`
  event. Later readings update evidence and metrics; they do not re-alert.

The coverage and minimum-reading checks matter. Without them, two spike readings
30 minutes apart could satisfy a naive "elapsed time" rule. With them, the engine
requires evidence density proportional to the window.

### Gap handling

- A gap `<= 15 min` is tolerated: T-04's missing 08:35 reading yields a
  10-minute gap and the 08:25-08:45 run still qualifies on elapsed time and
  coverage.
- A gap `> 15 min` **breaks a PENDING run**, because continuity cannot be proven
  and a false alert is worse than waiting for more data.
- A gap `> 15 min` **does not resolve an ACTIVE run**. The incident stays open,
  the gap is recorded in the evidence, and recovery evidence is required after
  data resumes. Missing data is not the same as cold cargo.

### Data-quality reporting

The engine repairs three things before it evaluates a truck: it sorts by event
time, collapses duplicate timestamps, and measures gaps. Those repairs should not
be invisible, so `quality.py` produces a report per truck and in aggregate:
raw vs. unique readings, out-of-order readings, duplicate timestamps, expected
vs. missing samples for the observed span, and every gap above the nominal
cadence with its tolerance verdict.

`coldchain replay` prints this as a **DATA QUALITY** section, and the JSON
analysis payload exposes it as `data_quality`. This lets an operator see that,
for example, T-04's alert rests on a run containing one missing sample, while
T-05's alert came from data that arrived out of order but evaluated identically
after event-time sorting. It is also the natural input to a future stale-sensor
alert.

### Sensitivity analysis

Threshold and duration are risk decisions, so `coldchain what-if` sweeps them
against the real dataset. It prints incident counts for each
threshold × duration combination and lists the affected trucks, with
`--format json` for scripting. It is read-only: a chosen combination is written
to a policy file, then validated by replay and tests. This keeps the “why 8.0 C
and 20 minutes?” conversation evidence-based rather than rhetorical.

### How the seed data is handled

| Truck | Data story | Result |
|---|---|---|
| T-01 | Clear sustained rise from 08:10, peak 9.6 C, cooling from 08:45 | Alert at 08:30 once 20 minutes of evidence exists; resolved at 08:50 |
| T-02 | One 8.9 C spike at 08:10, back to 6.5 C at 08:15 | Suppressed as a brief excursion |
| T-03 | Never exceeds 7.0 C | No incident |
| T-04 | Two short blips, then a 20-minute run with a missing 08:35 reading | First blip suppressed; second run alerts despite the 10-minute gap, still open at end of data |
| T-05 | Same shape as T-01, readings shuffled | Sorted by event time; same alert as T-01 |

The replay therefore reports **3 incidents, 2 suppressed blips, 1 quiet truck**.
A second replay produces **0 new events**.

## 4. Trade-offs

- **Rule-based, not ML.** A deterministic rule is explainable to ops, testable,
  and auditable. An ML anomaly detector could catch unusual patterns, but it
  would be harder to defend as the first production alert path and needs labeled
  incident data that Nimbus does not yet have.
- **Sample-based duration rather than integral/area-under-curve.** With 5-minute
  readings, a time-weighted exposure integral is more precise but adds
  interpolation assumptions and is harder for an ops person to verify. The
  current rule is deliberately conservative and transparent; time-weighted
  exposure is a good v2 enhancement.
- **Recompute per truck vs. incremental streaming.** Recomputing a truck's
  history on ingest makes late/out-of-order data correctness trivial and the
  implementation small. It is appropriate for this fleet and demo; at very high
  throughput, use event-time windows, watermarks, and incremental state.
- **SQLite vs. managed storage.** SQLite gives a reproducible one-command demo
  and durable deduplication. Production should move to a time-series/event store
  with retention, replication, and a durable notification outbox.
- **20-minute default.** This is a product judgment. A pharma customer may want
  a shorter window for a high-value load; a grocery customer may accept longer.
  The policy file, per-truck overrides, and `coldchain what-if` exist so this is
  a configuration conversation backed by evidence, not a code change.
- **No separate sensor-health alert in v1.** It is arguably the most important
  missing reliability feature: a truck that goes silent is not safe just because
  it is not warm. Gaps are recorded, but a dedicated "stale sensor" alert is
  future work.
- **No acknowledgement/escalation workflow.** The system produces trustworthy
  incidents; how a human claims, escalates, and closes them is a separate product
  surface.

## 5. What I would do with more time / data / budget

1. **Per-cargo policies and customer SLAs.** Add cargo type and setpoint to the
   reading/load metadata, with policies by cargo/customer rather than truck ID.
2. **Sensor-health and staleness alerting.** Raise a separate incident when a
   truck has not reported within `N` expected intervals. This is different from a
   temperature excursion and should have its own runbook.
3. **Time-weighted exposure and severity.** Integrate degrees-minutes above the
   limit, and incorporate cargo tolerance, door-open events, and compressor
   telemetry into severity.
4. **Notification and escalation integrations.** Slack/PagerDuty/email, durable
   outbox, retries, on-call routing, acknowledgement, and suppression windows.
5. **Backtesting and tuning.** Replay months of labeled incidents to tune
   thresholds per cargo lane, measure false-positive/false-negative rates, and
   quantify alert reduction versus the current per-reading system.
6. **Production event architecture.** Kafka/Kinesis ingestion, event-time
   windows, per-truck ordering, a time-series store, dead-letter handling, and
   observability around ingest lag, gap rate, incident rate, and alert latency.
7. **Security and multi-tenancy.** Authentication, tenant isolation, audit logs,
   and retention/compliance controls for pharma records.

The delivered code is intentionally small enough to defend line-by-line, but the
boundaries (`engine`, `store`, `pipeline`, `api`) are where those production
concerns would attach.
