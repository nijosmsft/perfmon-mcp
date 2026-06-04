# Changelog

All notable changes to perfmon-mcp are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-06-02

### Added
- `get_rss_distribution(log_id, adapter_filter, scenario_hint)` — RSS-specific
  lens scoped to the 13 curated Mellanox WinOF-2 RSS counter names; split into
  per-CPU + per-RqNum + per-SqNum sections with hot/idle counts in each
  section header.
- `discover_counter_instances(set_name, instance_filter, target)` — enumerate
  per-instance counter paths for one PDH counter set via
  `(Get-Counter -ListSet '<set>').PathsWithInstances`; supports a regex filter
  (e.g. `'Adapter #2'`).
- `parse_counter_instances_output(text, set_name, instance_filter)` — sibling
  parser for remote stdout.
- `get_teardown_commands(collector_name, target)` — emit-only force-cleanup
  runbook for the managed collector + straggler perfmon / typeperf / relog /
  logman processes.
- `parse_teardown_output(text, collector_name)` — sibling parser for remote
  teardown stdout.
- `compute_rate_from_counter(log_id, counter_filter, interval_s)` — per-counter
  rate aggregator for monotonic raw totals:
  `(last - first) / DurationSeconds`.
- `get_per_queue_summary` now surfaces `Delta`, `MaxMinRatio`, `Hot`, and
  `Idle` peer-group flags plus a footer summary.
- `instance_filter` keyword argument on `get_capture_commands` /
  `get_capture_instructions`, narrowing per-instance counter enumeration.
- Mellanox NIC RSS workflow absorbed the standalone `analyze-mellanox-rss`
  PowerShell skill into a discoverable MCP surface so the LLM can drive every
  step through tool calls without leaving the model loop.

### Changed
- Profile metadata now carries `priority_metrics` and
  `default_instance_filter`. `mellanox-percpu` defaults `instance_filter` to
  `'Adapter #2'`.

## [0.2.0] - 2026-06-02

### Added
- `analyze(log_id, sections)` mega-tool that composes summary + timeline +
  per-queue overview into one call.
- Log registry tools: `load_log`, `load_csv`, `unload_log`, `log_info`,
  `list_loaded_logs`, `list_blgs`.
- Discovery tools: `discover_counter_sets` (vendor-tagged),
  `discover_nics`, `get_capture_status` (managed collector query).
- `get_counter_throughput` — NIC throughput convenience lens narrowing the
  per-counter summary to Network Adapter throughput rows.
- LabLink-first remote runbooks (with JSON sidecars carrying `parse_with`,
  `shell`, `expected_runtime_s`, `timeout_s`) across every emit-style tool.

### Deprecated
- `load_blg` — preserved as an alias of `load_log` for v0.2 compatibility;
  emits a `DeprecationWarning`.
