# Freeze-guard stats

Analysis of `logs/freeze-guard.log` and `logs/latency-history.tsv`.

Observation window **2026-08-09 05:25–18:15**, covering several guard
generations as the script was iterated on that morning, then ~9.5h of the
current generation. Both files are appended to live and the guard was still
running at time of writing, so counts drift between reads; all figures below
are from the **18:15 snapshot** unless noted.

## Data caveat: the running guard is older than HEAD

The live guard is PID 21971, started **08:33:02**, running
`~/Library/Application Support/BTT/btt-freeze-guard.sh`. That file on disk
is byte-identical to HEAD, but the process parsed it at 08:33 and therefore
predates these commits:

- `6dc2fc3` (08:55) duration-format restart markers + lyrics-trace check
- `90b58fa` (09:09) latency probe timeout shortened to 22s
- `cc2e82c` (09:16) lyrics value-mismatch check
- `78dff1b` (09:20) player watcher removed

Three independent confirmations from the log: it reports `unresponsive for
30.000000s` where HEAD sets `DEFAULT_LATENCY_TIMEOUT=22`; its `responsive`
lines carry no lyrics field at all; and it writes epoch-timestamp restart
markers. **None of the last four commits are live.**

**Consequence for the `+` marker rows in `latency-history.tsv`:** at HEAD,
`write_restart_marker` writes `restart_duration` (seconds of widget-sample
coverage for the session just ended). The running process still writes the
pre-`6dc2fc3` `date +%s` epoch. Every `+` row currently in the file is a
restart-initiation **timestamp**, not a duration — and the format changes
the first time the guard itself is restarted. Anything parsing this file
must handle both.

Guard generations are identifiable from the log vocabulary:

| message | window | guard |
| --- | --- | --- |
| `timer-widget unresponsive for Ns` | 05:27–08:13 | old, timer-trace |
| `clash-latency timestamp missing for Ns` | 08:14–08:29 | interim |
| `clash-latency refresh unresponsive for N.Ns` | 08:29–18:15 | current |

## Restart events

**206 completed restarts** in `freeze-guard.log` over 05:35–18:15. Split by
generation: 77 from the old timer-trace guard, **129 from the current
guard** — which matches the 129 `+` markers in `latency-history.tsv`
exactly. Each marker precedes its `restart refresh sequence complete` line
by 21–41s.

| | restarts | span | rate | mean gap | median gap | min / max |
| --- | --- | --- | --- | --- | --- | --- |
| Old (timer-trace) | 77 | 05:35–08:27 | 26.9/h | 136s | 90s | 56s / 500s |
| Current (latency probe) | 129 | 08:43–18:15 | 13.5/h | 268s | **111s** | 80s / 1987s |

Probe outcomes under the current guard: **173 unresponsive vs 192
responsive — BTT fails the latency probe 47% of the time**, across 365
probes. That, not the restart count, is the cleanest statement of how often
BTT is wedged.

## The intervals are quantized, not continuous

This is the main structural finding. Restart intervals are not a smooth
distribution — they cluster on `81 + 30k` seconds:

| interval (±3s) | k | count | share |
| --- | --- | --- | --- |
| 81s | −1 | 10 | 8% |
| **111s** | **0** | **59** | **46%** |
| 141s | 1 | 16 | 12% |
| 171s | 2 | 3 | 2% |
| other ≤174s | — | 15 | 12% |
| >174s | 5…65 | 25 | 20% |

**59% of all intervals are exactly congruent to 21 mod 30**, and 66% fall in
the 81/111/141s bins above. The 30s step is `LATENCY_TIMEOUT` in the running
process — the loop sleeps it unconditionally on every iteration, so each
additional probe cycle before the next failure adds exactly 30s. The spike
at exactly 111s is stark: 49 of 128 intervals are that precise value.

The 111s floor is the guard's minimum possible restart period, and it is by
far the most common outcome:

```text
sleep LATENCY_TIMEOUT   30s   probe window
sleep 3 + sleep 1        4s   quit, then killall -9
sleep 5 + 5 + 5         15s   refresh_widgets retries
sleep RESTART_STARTUP_GRACE  30s   startup grace
                        ---
                         79s  one restart cycle
+ next probe window     30s
                        ---
                        109s  ~= observed 111s mode
```

**A 111s interval means the guard restarted BTT, probed once, found it still
unresponsive, and restarted again.** 38% of all intervals are exactly that
value, 46% within ±3s of it. For long stretches (09:00–13:00, median gap
111s every hour) the guard was in a continuous restart loop, giving BTT no
chance to stay up.

## Trend

Hourly restart counts and median gaps, current guard:

| hour | restarts | median gap | deferred | unresp | resp |
| --- | --- | --- | --- | --- | --- |
| 08:00 | 3 | 483s | 94 | 19 | 36 |
| 09:00 | 19 | 111s | 27 | 46 | 41 |
| 10:00 | 30 | **111s** | 0 | 31 | 37 |
| 11:00 | 20 | **111s** | 0 | 19 | 18 |
| 12:00 | 23 | **111s** | 0 | 23 | 27 |
| 13:00 | 7 | 112s | 0 | 8 | 3 |
| 14:00 | 7 | 131s | 0 | 7 | 6 |
| 15:00 | 7 | 210s | 0 | 7 | 8 |
| 16:00 | 6 | 316s | 0 | 5 | 5 |
| 17:00 | 4 | 1130s | 0 | 5 | 6 |

Whole-run OLS on interval vs index gives **+3.64s per restart** (r² = 0.14),
fitting 37s → 499s. Taken at face value that says intervals are lengthening
and things are improving. **They are not — see below.** The morning
(09:00–12:00) is pinned at the 111s floor; the afternoon looks better only
because the machine was mostly asleep.

An earlier reading of this file, made at 09:27 with only 6 intervals
available, projected the new guard's intervals as contracting toward the old
guard's failure mode (slope −1.7 min/restart). **The full data refutes
that** — the intervals hit the 111s hard floor and stayed there rather than
continuing down. n=6 was not enough to see a floor.

## Long intervals are system sleep, not recovery

The interval distribution is sharply bimodal:

| mode | n | share | mean | median |
| --- | --- | --- | --- | --- |
| short (<300s) | 107 | 84% | 121s | 111s |
| long (≥300s) | 21 | 16% | 1017s | 1121s |

The long mode clusters tightly at 1009–1245s (~17–21 min), which does not
match any constant in the script. It matches `pmset -g log`: the Mac spent
the afternoon in repeated `Entering Sleep` → `DarkWake from Deep` cycles
whose durations cluster at **15.2–17.8 min**.

Two direct measurements:

- **20 of 21 long intervals (95%) contain a sleep span.**
- **19 of 20 DarkWakes after 13:00 (95%) are followed by a restart within
  120s.**

Probe throughput corroborates it: ~68 probes/hour at 10:00–12:00 versus ~11
probes/hour at 17:00, consistent with the guard process being suspended
while asleep.

So the afternoon's longer gaps are wall-clock artifacts — fewer awake
minutes in which to restart — and on top of that **every wake produces a
restart**. The guard cannot distinguish "widget stale because BTT is wedged"
from "widget stale because the system was asleep and nothing ran", so it
treats each wake as a freeze. This is a false-positive source the current
logic has no defence against.

(Precise restarts-per-awake-hour is not reported here: reconstructing sleep
spans from `pmset` output proved unreliable — naive `Sleep`→next-`Wake`
pairing implies the machine was asleep during hours when probes were
demonstrably dense. The two percentages above are measured directly against
wake timestamps and do not depend on that reconstruction.)

## The keyboard-idle deferral gate

Under the **old** guard the gate dominated everything: **424 triggers → 84
restarts, 97.9% deferred**, essentially all via `restart deferred --
keyboard activity within 1s` (`actions/btt-freeze-guard.sh:160-165`). The
guard concluded "BTT is wedged" 424 times in under 4 hours; what decided
whether a restart happened was whether the user paused typing for one
second.

Under the **current** guard the gate is nearly inert: **173 triggers → 129
restarts, only 37 deferred** — and 36 of those 37 fall before 10:00 (the
lone exception is one at 18:13:32). Across 09:00–18:00 essentially nothing
suppressed the restart loop: the user stopped typing and the machine started
sleeping. The gate does no work precisely when the loop runs hottest.

## Latency samples (secondary)

Sampled 08:13:34–09:23:21, 415 samples, all endpoint `SG Freddo`.

| metric | value |
| --- | --- |
| mean | 157.0 ms |
| median | 149 ms |
| stdev | 31.3 ms |
| min / max | 136 / 400 ms |
| p75 / p90 / p95 / p99 | 152 / 203 / 221 / 277 ms |

Tight baseline plus a spike tail, not a bell curve: 85% of samples fall in
136–160 ms; 10.4% exceed 200 ms and pull the mean 8 ms above the median.

**No latency trend.** OLS gives +0.30 ms/min but r² = 0.027, Spearman
ρ = 0.126; the 10-minute rolling median holds at 145–153 ms across the whole
window. What varies is spike frequency, and it oscillates (12.5% → 0% →
15.2% → 18.2% → 9.8%) rather than trending. **Network latency is not the
cause of the freezes.**

Two measurement caveats:

- **Samples are not independent.** Consecutive rows repeat identical values
  (277 ms five times across 08:40:01–08:40:37), i.e. the widget value is
  re-read rather than re-probed. Collapsing consecutive duplicates leaves 84
  distinct readings, mean 162.0 ms — the raw mean is biased low because fast
  baseline values get re-sampled more often during dense-cadence periods.
- **Cadence collapses late in the window.** Median inter-sample gap goes
  2.2s → 23.8s → 44.7s after ~50 min, max 281s. The two largest spikes
  (400 ms at 09:12:57, 392 ms at 09:23:21) both land in that sparse region.

## Open items

1. **Restart the guard process** so the last four commits go live. The 22s
   timeout and the lyrics-value check both target the false-positive problem
   the 47% unresponsive rate points at, and neither is running.
2. **Suppress wake-triggered restarts.** 95% of post-13:00 wakes cause a
   restart. The guard needs to skip one probe cycle after a wake, or compare
   the widget timestamp against wake time rather than against wall clock.
3. **Break the 111s restart loop.** A 111s interval means the restart did
   not help. Consecutive floor-interval restarts should back off rather than
   retry immediately — 46% of all restarts were this case.
4. **Log the trigger rate directly.** Restart count is a lagging, gated
   proxy; unresponsive-vs-responsive probe ratio (47%) is the real health
   signal and is currently only recoverable by parsing prose log lines.

## Reproduction

```sh
# completed restarts, and deferrals
grep -c "restart refresh sequence complete" logs/freeze-guard.log
grep -c "restart deferred" logs/freeze-guard.log

# probe outcome ratio (current guard)
grep -c "clash-latency refresh unresponsive" logs/freeze-guard.log
grep -c "clash-latency refresh responsive"   logs/freeze-guard.log

# restart markers (format depends on the running guard's vintage, see above)
grep -c "^+" logs/latency-history.tsv

# sleep/wake correlation
pmset -g log | grep -E "Entering Sleep|DarkWake from" | grep "^2026-08-09 1[3-8]:"
```
