"""
Small utility module provided as an optional starting point for the
cold-chain alerting case study.

Feel free to use, modify, replace, or ignore this entirely -- it is not
guaranteed to be complete or bug-free.
"""
from datetime import datetime

READING_INTERVAL_MINUTES = 5


def load_readings(path):
    """Load the raw list of sensor readings from a JSON file. Not sorted."""
    import json
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("readings", [])


def parse_timestamp(ts):
    return datetime.fromisoformat(ts)


def is_violation(reading, threshold_c=8.0):
    return reading["temperature_c"] > threshold_c


def group_violation_episodes(readings, threshold_c=8.0):
    """
    Group consecutive violating readings per truck into episodes.

    Assumes `readings` for each truck are already sorted by timestamp --
    this function does not sort for you.
    """
    episodes = []
    current_episode = []
    current_truck = None
    for reading in readings:
        truck_changed = reading["truck_id"] != current_truck
        if truck_changed and current_episode:
            episodes.append(current_episode)
            current_episode = []
        current_truck = reading["truck_id"]

        if is_violation(reading, threshold_c):
            current_episode.append(reading)
        elif current_episode:
            episodes.append(current_episode)
            current_episode = []
    if current_episode:
        episodes.append(current_episode)
    return episodes


def episode_duration_minutes(episode):
    """Duration of a violation episode, in minutes."""
    # Assumes readings arrive at a fixed 5-minute cadence with no gaps.
    return (len(episode) - 1) * READING_INTERVAL_MINUTES


def find_alertable_episodes(readings, threshold_c=8.0, min_duration_minutes=20):
    """Return violation episodes that meet the minimum sustained duration."""
    episodes = group_violation_episodes(readings, threshold_c)
    return [ep for ep in episodes if episode_duration_minutes(ep) >= min_duration_minutes]
