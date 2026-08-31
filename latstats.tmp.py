"""Offline analysis of live runtime_event turn latencies (p50/p95 per model)."""

import json
from collections import defaultdict
from datetime import datetime

rows = []
with open("turns.tmp.tsv", encoding="utf-8-sig") as fh:
    for line in fh:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 4:
            continue
        run_id, etype, ts, payload = parts[0], parts[1], parts[2], "\t".join(parts[3:])
        try:
            data = json.loads(payload)
            when = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f")
        except (ValueError, json.JSONDecodeError):
            continue
        rows.append((run_id, etype, when, data))

starts = {}
durations = defaultdict(list)
for run_id, etype, when, data in rows:
    step = data.get("step")
    key = (run_id, step)
    if etype == "model_turn_start":
        starts[key] = (when, data.get("model") or "unknown")
    elif etype == "model_turn_end":
        pair = starts.pop(key, None)
        if pair is None:
            continue
        ms = (when - pair[0]).total_seconds() * 1000
        if ms < 0:
            continue
        model = data.get("model") or pair[1] or "unknown"
        durations[model].append(ms)


def pct(values, q):
    values = sorted(values)
    idx = max(0, min(len(values) - 1, int(round(q * (len(values) - 1)))))
    return values[idx]


print(f"{'model':<28}{'turns':>7}{'p50_ms':>10}{'p95_ms':>10}{'max_ms':>10}")
for model, values in sorted(durations.items(), key=lambda kv: -len(kv[1])):
    print(
        f"{model:<28}{len(values):>7}{pct(values, 0.50):>10.0f}"
        f"{pct(values, 0.95):>10.0f}{max(values):>10.0f}"
    )
total = [v for vs in durations.values() for v in vs]
if total:
    print(
        f"{'ALL':<28}{len(total):>7}{pct(total, 0.5):>10.0f}"
        f"{pct(total, 0.95):>10.0f}{max(total):>10.0f}"
    )
