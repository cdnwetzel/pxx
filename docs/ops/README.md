# Ops: running the pxx improvement daemon under launchd (macOS)

The improvement daemon runs a **propose-only** cycle: it mines terminal run
records, clusters them, and writes improvement **proposals** into the triage
inbox for human review. It never edits the working tree, runs the agent, or
promotes anything (`stopped_before_promotion` is pinned true). The cycle is
deterministic and offline — it needs no model endpoint, so it does not depend
on any inference box being up.

See receipt **R-022** for the live verification.

## Install (LaunchAgent, hourly `--once`)

`local.pxx.improve-daemon.plist` is a reference LaunchAgent. **Adjust the
absolute paths** for your machine (the `pxx` binary, `WorkingDirectory`, `HOME`,
and the log path), then:

```sh
cp docs/ops/local.pxx.improve-daemon.plist ~/Library/LaunchAgents/
plutil -lint ~/Library/LaunchAgents/local.pxx.improve-daemon.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.pxx.improve-daemon.plist
launchctl print gui/$(id -u)/local.pxx.improve-daemon | grep -i state
```

- `WorkingDirectory` must be the repo (or wherever `load_settings(cwd)` resolves
  your `state_dir`) so the daemon uses the intended state dir.
- Runs once per hour (`StartCalendarInterval` Minute 0); the process exits
  between ticks. `pxx improve status` therefore reads `daemon: stopped` between
  ticks by design — that is correct for the `--once`/cron model, not a liveness
  regression. (For a process that holds `daemon.lock` continuously and reads
  `running`, drop `--once`, add `KeepAlive`, and set `--interval 3600`.)

## Verify

```sh
launchctl kickstart -k gui/$(id -u)/local.pxx.improve-daemon   # run one tick now
tail -1 ~/Library/Logs/pxx-improve.log                          # -> ticks=1 cycles=1
pxx improve status                                              # inbox proposals; daemon: stopped
```

## Operate

```sh
pxx improve pause     # skip the cycle at the next tick (durable); log shows paused-skips
pxx improve resume    # clear the pause
pxx improve status    # cycle / queue / inbox / daemon liveness
```

## Uninstall

```sh
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.pxx.improve-daemon.plist
rm ~/Library/LaunchAgents/local.pxx.improve-daemon.plist
```

## What it does and does not accrue

The daemon accrues **proposals for human triage**. It does **not** advance the
earned-enablement `real_runs` or `human_approved_promotions` bars — those move
only from genuine `pxx` agent runs and human promotions. Auto-promotion remains
report-and-refuse until those bars are earned.
