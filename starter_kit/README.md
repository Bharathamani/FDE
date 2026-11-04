# Starter Kit — Cold-Chain Alerting Engine

## Contents

- `data/sensor_readings.json` — ~55 synthetic temperature/humidity readings across 5 refrigerated trucks (T-01 through T-05) over about an hour, at roughly 5-minute intervals.
- `alert_utils.py` — a small, optional Python utility module with basic violation-detection and episode-grouping helpers, provided as a rough starting point.

## A few notes

- You're free to use, modify, replace, or ignore `alert_utils.py` entirely — it's not guaranteed to be complete, optimal, or bug-free.
- `group_violation_episodes` explicitly assumes readings are already sorted by timestamp per truck — it does not sort for you. Worth checking whether that assumption actually holds for all the data provided.
- Not every truck's data tells the same story — some are straightforward, some aren't. Part of the exercise is noticing which is which.
