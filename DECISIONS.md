# Decision Register — “Too Warm,” “Too Long,” Gaps, and Out-of-Order Data

The original ask was deliberately incomplete. This document makes every missing
decision explicit, states why the default was chosen, and records how to change
it. The defaults below are **product assumptions for sign-off**, not physical
laws. All times are UTC and all temperatures are Celsius.

Status: **proposed defaults, implemented and covered by tests**. The customer
sign-off fields are at the end.

---

## Decision summary

| # | Question | Decision | Default |
|---|---|---|---|
| D1 | What counts as “too warm”? | A reading is a breach when it is **strictly greater than** the configured ceiling. | `> 8.0 C` |
| D2 | What counts as “too long”? | A breach run alerts when it spans at least the sustained window **and** has enough real breach evidence. | `>= 20 min`, `>= 3` breach readings, `>= 50%` coverage |
| D3 | What is the alert unit? | One **incident** per truck episode, not one alert per reading. | One `OPENED`, at most one `RESOLVED` |
| D4 | How are sensor gaps handled? | Small gaps are tolerated; a large gap breaks a **pending** run but never silently resolves an **active** incident. | tolerance `<= 15 min` |
| D5 | How is out-of-order data handled? | Evaluate in **event time** after sorting, collapse duplicate timestamps, recompute the truck, keep incident identity stable. | last write wins per timestamp |
| D6 | When is an incident over? | After consecutive clear readings below the recovery threshold. | `2` readings `<= 7.5 C` |
| D7 | How is bad data made visible? | Report what was repaired instead of silently cleaning it. | `coldchain replay` data-quality section |
| D8 | What clock/timezone is assumed? | ISO-8601 timestamps; naive timestamps are treated as UTC; output is UTC. | UTC |

---

## D1 — What counts as “too warm”?

**Decision:** A reading is a breach when `temperature_c > threshold_c`.
The default `threshold_c` is **8.0 C**. A reading of exactly 8.0 C is **not** a
breach.

**Why 8.0 C:** it is a common cold-chain ceiling for refrigerated
pharma/grocery cargo. The provided dataset has no cargo type, customer, or
setpoint metadata, so a single fleet-wide default is the only defensible start.
Per-truck overrides already exist; per-cargo policies are the next step.

**Why strict `>` rather than `>=`:** the threshold is a ceiling, not a target.
Treating 8.0 C as safe avoids alerting on a sensor that is sitting exactly on the
limit. The recovery threshold is lower (D6), so a truck hovering at the boundary
does not flap.

**What is measured:** each individual reading, not a rolling mean. A rolling mean
could hide a short but genuine spike, and it makes the evidence harder for an ops
person to verify against the raw feed. Time-weighted exposure is future work.

**Not decided here:** cargo-specific limits, probe placement, and calibration
error. Those require customer data and are listed as open questions.

---

## D2 — How long is “too long”?

**Decision:** a breach run becomes an alert only when **all** of these are true:

1. elapsed event time from the first to the latest breach is
   `>= min_duration_minutes` (**20 minutes** by default);
2. there are at least `min_breach_readings` (**3**) readings above the threshold;
3. breach readings cover at least `min_coverage` (**50%**) of the samples
   expected at the nominal cadence; and
4. no gap inside the run exceeds the gap tolerance (D4).

Exactly 20 minutes qualifies. Duration is measured from the **first breach
reading to the last breach reading**, not from the alert detection time and not
from the number of readings.

**Why 20 minutes:** long enough to ignore a single probe spike or a door-open
blip, short enough to act before most cargo is meaningfully exposed. It is a
commercial risk decision, not a sensor property.

**Why the extra evidence rules:** elapsed time alone is trivially fooled by two
spike readings 30 minutes apart. Requiring at least three actual breach readings
and 50% coverage forces the data to support the claim that the excursion was
continuous. These rules also prevent a sparse or broken sensor stream from
fabricating an incident.

**Why not “N consecutive readings”:** that silently assumes a perfect 5-minute
cadence. The provided data already violates that assumption (T-04 is missing a
reading), so duration is computed from event time instead.

**Worked boundary:** a run from 08:10 to 08:30 with breaches at 08:10, 08:15,
08:20, 08:25, and 08:30 is 20 minutes, 5 breach readings, 5 of 5 expected
samples, so it alerts. A run from 08:10 to 08:25 with only the two endpoint
readings is 15 minutes and 2 breach readings, so it does not.

---

## D3 — What is the alert unit?

**Decision:** the unit is an **incident**, identified by
`INC-<hash(truck_id + episode start)>`.

- Exactly one `OPENED` event is emitted when the sustained condition is met.
- At most one `RESOLVED` event is emitted when recovery evidence is complete.
- Additional readings update the same incident's evidence, peak, and exposure;
  they do not re-alert.
- If a late reading proves an episode was not real, the incident is marked
  `SUPERSEDED` rather than deleted, preserving the audit trail.

**Why:** the original problem was alert fatigue from per-reading alerts. One
incident per real episode is the product requirement, and deterministic identity
plus a unique `(incident_id, event_type)` constraint makes replay and retries
idempotent.

---

## D4 — How should the system behave when data has gaps?

**Decision:** distinguish **pending** evidence from an **active** incident.

- Nominal cadence is **5 minutes** (`nominal_interval_minutes`).
- A gap is any interval between consecutive readings larger than the nominal
  cadence.
- A gap up to **15 minutes** (`max_gap_minutes`) is tolerated. That covers one
  missed 5-minute sample with margin for jitter.
- A gap **greater than 15 minutes** while the run is still `PENDING` **breaks the
  run**. Continuity cannot be proven, so the episode is suppressed with reason
  code `data_gap` and a new run must start after data resumes.
- A gap **greater than 15 minutes** while the incident is `ACTIVE` **does not
  resolve it**. The incident stays open, the gap is recorded in the evidence,
  and `recovery_count` is reset so fresh clear readings are required after data
  returns.
- Missing data is never treated as cold cargo.

**Why 15 minutes:** it permits one missing 5-minute reading plus schedule jitter,
while refusing to call a half-hour blackout “continuous.” It is deliberately
shorter than the 20-minute sustained window: a gap that long could otherwise
contain the entire evidence period.

**Consequence:** a truck that goes silent forever stays `OPEN`. That is
intentional and safer than a false resolution, but it is not sufficient
operationally: a dedicated “sensor stale/offline” alert is listed as future work.

---

## D5 — How should out-of-order data be handled?

**Decision:** the engine is **event-time based**.

1. Readings are grouped per truck and sorted by timestamp before evaluation.
2. Duplicate timestamps are collapsed to one reading; the **last value seen for
   that timestamp wins** (upsert semantics). This makes sensor retries
   idempotent.
3. After sorting, the same state machine runs regardless of arrival order.
4. A late reading triggers recomputation of that truck from persisted history.
   Incident IDs are deterministic, so an update cannot create a duplicate alert.
5. If recomputation invalidates an existing episode, it is `SUPERSEDED`.

**Why:** T-05 in the provided dataset is T-01's shape with shuffled arrival
order. Sorting by event time reproduces exactly the same incident. Recomputing a
truck is simple and correct at this fleet size; at very high throughput it would
be replaced by event-time windows with watermarks and incremental state.

**What is not repaired:** the system does not invent missing readings, smooth
curves, or interpolate temperatures. It only orders, deduplicates, and reports.

---

## D6 — When is an incident over?

**Decision:** an active incident resolves after **2 consecutive readings at or
below 7.5 C** (`recovery_readings = 2`, `clear_threshold_c = 7.5`).

The band `(7.5, 8.0]` is deliberate hysteresis:

- it is **not a new breach**, so it does not add breach evidence;
- it is **not recovery**, so it keeps an active incident open;
- it resets any partial recovery count.

**Why:** a single cool reading can be a probe artifact, and a truck hovering at
the limit should not flap between open and resolved. Two consecutive clear
readings give modest confirmation without delaying resolution by much.

---

## D7 — How is bad data made visible?

**Decision:** `coldchain replay` prints a **DATA QUALITY** section, and the JSON
analysis payload includes `data_quality`. The report is computed from the input
readings before the engine's canonical sort.

It reports, per truck and in aggregate:

- raw readings vs. unique `(truck, timestamp)` readings;
- duplicate timestamps collapsed;
- out-of-order readings;
- expected samples for the observed time span;
- missing expected samples;
- each gap larger than the nominal cadence and whether it is within tolerance.

**Why:** silent cleaning hides the fact that an alert decision was made on
repaired data. The report lets an FDE say “this incident is real, and here is the
sensor-quality caveat.” It is also the input to the future stale-sensor alert.

**Definition of missing samples:** for the observed first-to-last span,
`expected = floor(span / nominal_interval) + 1`, and
`missing = max(0, expected - unique_timestamps)`. It is a cadence-based
completeness measure, not a claim about what the temperature actually was.

---

## D8 — What clock and timezone are assumed?

**Decision:** timestamps are ISO-8601. A timestamp with no offset is interpreted
as UTC. All stored and displayed timestamps are UTC. Arrival time is recorded for
audit but never used for alert timing.

**Why:** the dataset has naive timestamps and the fleet may span time zones.
Using arrival time would make late or buffered sensor uploads change alert
outcomes.

---

## Configuration and how to change a decision

Defaults live in `Policy` in `src/coldchain/config.py`. They can be changed by a
JSON policy file (including per-truck overrides) or environment variables.

| Decision | Policy field | Environment variable | Default |
|---|---|---:|---:|
| D1 breach ceiling | `threshold_c` | `COLDCHAIN_THRESHOLD_C` | `8.0` |
| D6 recovery ceiling | `clear_threshold_c` | `COLDCHAIN_CLEAR_THRESHOLD_C` | `7.5` |
| D2 sustained window | `min_duration_minutes` | `COLDCHAIN_MIN_DURATION_MIN` | `20` |
| D2 minimum evidence | `min_breach_readings` | `COLDCHAIN_MIN_BREACH_READINGS` | `3` |
| D2 evidence density | `min_coverage` | `COLDCHAIN_MIN_COVERAGE` | `0.5` |
| D4 gap tolerance | `max_gap_minutes` | `COLDCHAIN_MAX_GAP_MIN` | `15` |
| D6 recovery evidence | `recovery_readings` | `COLDCHAIN_RECOVERY_READINGS` | `2` |
| D4 expected cadence | `nominal_interval_minutes` | `COLDCHAIN_NOMINAL_INTERVAL_MIN` | `5` |

Precedence: built-in defaults < policy-file default < environment variables <
per-truck file overrides.

Example per-truck override:

```json
{
  "default": { "threshold_c": 8.0, "min_duration_minutes": 20 },
  "trucks": {
    "T-04": { "threshold_c": 7.0, "min_duration_minutes": 15 }
  }
}
```

Changing a default is a policy decision, so the expected workflow is:

1. Run `coldchain what-if --data data/sensor_readings.json` to see how incident
   counts and affected trucks move across threshold × duration combinations.
2. Choose a combination with the customer, considering false positives vs.
   detection delay.
3. Put the chosen values in a policy file, not in code.
4. Replay the golden dataset and confirm the expected incidents still hold.
5. Run `pytest -q`.

### Sensitivity example

`coldchain what-if` sweeps the two signed-off knobs around the current policy
(baseline ±1.0 C in 0.5 C steps, baseline ±10 minutes in 5-minute steps). It
shows incident counts in a grid and lists which trucks appear in each
combination. Use `--thresholds`, `--durations`, and `--format json` to script a
specific review.

The purpose is to make the trade-off explicit: lowering the threshold or
shortening the window catches more trucks earlier and increases noise; raising it
or lengthening it does the opposite. The command does not change configuration.

---

## Worked result on the provided dataset

With the defaults:

| Truck | Data story | Decision |
|---|---|---|
| T-01 | 30 minutes above 8.0 C, peak 9.6 C | Alert, resolved at 08:50 |
| T-02 | One 8.9 C spike, back to 6.5 C five minutes later | Suppressed as brief |
| T-03 | Peak 7.0 C | No breach |
| T-04 | Two short blips, then a 20-minute run with a missing 08:35 reading (10-minute gap) | First blip suppressed; sustained run alerts; still open at end of data |
| T-05 | Same shape as T-01, readings arrive out of order | Sorted by event time; same alert as T-01 |

Data-quality report: 54 readings, 54 unique timestamps, 5 out-of-order readings
(all T-05), 0 duplicate timestamps, 1 missing expected sample and one 10-minute
gap within tolerance (T-04), 0 gaps over tolerance.

Replaying the same data again produces **0 new events**.

---

## Open questions for the customer

These are deliberately **not** decided by this implementation:

1. **Cargo-specific limits.** Is 8.0 C right for every load, or should limits
   vary by cargo/customer/SLA? The policy file supports overrides now; cargo
   metadata would make it first-class.
2. **Probe placement and calibration.** Is the sensor in the cargo space or near
   a door/compressor? Calibration drift and probe failure need a separate
   reliability story.
3. **Stale-sensor policy.** How long should a truck be silent before raising a
   separate “sensor offline” incident? This is different from a temperature
   excursion and needs its own runbook.
4. **Regulatory evidence.** For pharma, what retention, audit-log, and
   tamper-evidence requirements apply?
5. **Acknowledgement/escalation.** Who owns an open incident, when does it
   escalate, and how is it closed?

---

## Sign-off

| Decision | Owner | Status | Date |
|---|---|---|---|
| D1 breach threshold | Nimbus ops + customer | Proposed | — |
| D2 sustained window and evidence | Nimbus ops + customer | Proposed | — |
| D3 one-alert-per-incident | Nimbus ops | Proposed | — |
| D4 gap tolerance | Nimbus ops + data engineering | Proposed | — |
| D5 event-time ordering | Engineering | Implemented | — |
| D6 recovery rule | Nimbus ops | Proposed | — |
| D7 data-quality reporting | Engineering | Implemented | — |
| D8 UTC assumption | Engineering | Implemented | — |
