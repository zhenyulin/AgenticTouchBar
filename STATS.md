# Freeze-guard stats

Analysis of `logs/freeze-guard.log`, `logs/latency-history.tsv`, and
`logs/trace.tsv`.

## Latest comparison

The latest complete guard-log record is **2026-08-10 02:33:46 +0800**. The
comparison uses two adjacent 12-hour wall-clock windows:

- **Prior:** 2026-08-09 02:33:46–14:33:46
- **Latest:** 2026-08-09 14:33:46–2026-08-10 02:33:46

### Restart frequency

| metric | prior 12h | latest 12h | change |
| --- | ---: | ---: | ---: |
| completed restarts | 183 | 103 | **−44%** |
| restart rate | 15.25/h | 8.58/h | **−44%** |
| median restart gap | 111s | 296s | **+167%** |
| mean restart gap | 172s | 421s | **+144%** |
| gaps under 180s | 155 | 31 | **−80%** |
| 10th–90th percentile gap | 62–276s | 122–1115s | shifted longer |

Restart frequency has improved substantially. The latest period is still not
healthy: 103 restarts in 12 hours is about one every seven minutes, and the
median gap remains short enough to indicate recurring freeze/restart episodes.
The change is nevertheless different from the previous 111-second restart
loop: only 31 latest gaps were under three minutes, compared with 155 before.

Probe outcomes also improved, but less decisively. The prior window had 1,022
unresponsive and 796 responsive outcomes, a **56.2% failure rate**; the latest
had 24 unresponsive and 27 responsive outcomes, a **47.1% failure rate**.
Probe volume fell sharply, so the rate comparison is more useful than the raw
counts. The latest sample is small and should be treated as provisional.

## Session-duration stability

`logs/trace.tsv` was grouped by `btt_pid`; each session duration is the elapsed
time between its first and last `timer-widget` trace row. Only sessions fully
contained within each window are included.

| metric | prior 12h | latest 12h | change |
| --- | ---: | ---: | ---: |
| complete timer sessions | 168 | 118 | lower restart churn |
| mean duration | 168.5s | 243.6s | **+45%** |
| median duration | 98.2s | 158.3s | **+61%** |
| p10–p90 duration | 28.2–158.0s | 18.2–388.5s | longer upper tail |
| duration standard deviation | 770.8s | 321.7s | **−58%** |
| coefficient of variation | 4.57 | 1.32 | substantially lower |
| sessions ≥300s | 5 | 27 | more sustained sessions |
| sessions ≥600s | 4 | 10 | more sustained sessions |

Session stability has improved in the operationally important sense: the
typical session lasts longer, the latest window has no prior-style multi-hour
outlier, and dispersion is much lower. The mean and standard deviation in the
prior period are distorted by one **9,847s** session. The latest distribution
still has a broad upper tail, so this is recovery from severe churn rather than
proof that BTT is stable for an entire workday.

## Data coverage and interpretation

`logs/latency-history.tsv` currently ends at **2026-08-09 18:15:02 +0800**,
while `logs/trace.tsv` and `logs/freeze-guard.log` continue to
**2026-08-10 02:33**. Its `+` rows therefore cover the earlier snapshot only;
they do not describe the latest 12-hour window. The latest restart counts in
this report come from `freeze-guard.log`, whose `restart refresh sequence
complete` records continue through 02:33.

The older marker rows are restart-initiation timestamps, not durations. The
format may change after the guard is restarted, so parsers must accept both
the legacy epoch form and the newer duration form.

Long gaps remain confounded by system sleep. A long wall-clock gap means the
guard may have been suspended, not that BTT remained healthy continuously.
The latest improvement should therefore be read together with awake-time
coverage once sleep/wake events are available for the same window.

## Current conclusions

1. Restart churn is down by roughly 44% in the latest 12 hours.
2. The 111-second restart loop is no longer dominant: sub-180-second gaps fell
   by 80%, and the median gap is now 296 seconds.
3. Typical BTT sessions are longer and less variable, with substantially fewer
   pathological short-session cycles.
4. BTT still fails nearly half of the available probes, and 103 restarts in a
   12-hour period remains excessive.
5. The latency-history writer needs to be checked or restarted; it has not
   recorded the newer guard activity after 18:15.

## Reproduction

```sh
# completed restarts
grep -c "restart refresh sequence complete" logs/freeze-guard.log

# probe outcomes
grep -c "unresponsive" logs/freeze-guard.log
grep -c "responsive" logs/freeze-guard.log

# restart markers; format depends on guard vintage
grep -c "^+" logs/latency-history.tsv

# latest trace records
tail -5 logs/trace.tsv
```
