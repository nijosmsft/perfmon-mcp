# Assumptions made during scaffold

Notes on judgment calls made while creating the v0.1.0 scaffold.

## `.cset` XML format

Microsoft's `logman create counter -xml` output uses a verbose namespaced
schema (`PerformanceCounterDataCollector` etc.). For v0.1 the vendored
`.cset` files use a minimal, hand-readable XML structure with just a
counter list, because the actual capture commands emitted by the MCP
build a counter list file (`-cf <txt>`) inline and don't depend on the
full `.cset` round-trip with logman. The metadata layer reads the .cset
purely as a counter source-of-truth.

If a future iteration wants drop-in compatibility with `logman import
-xml`, swap the file contents to the full schema; nothing in this MCP
parses the XML structurally (only the `<Counter>` text nodes).

## relog.exe shellout

Day-1 implementation only. The cache manifest reserves
`producer in {relog, native, csharp-pdh}` so a future C# PDH sidecar can
drop in without a schema migration. The current path uses
`subprocess.run(["relog.exe", "<blg>", "-o", "<csv>", "-f", "csv", "-t", "1"])`.

`-t 1` means "every sample" (no downsampling). For multi-GB logs this
may be slow; we cache the parsed parquet beside the .blg.

## Get-Counter parsing

The PowerShell `Get-Counter` text output is tab/space delimited and
includes a localized timestamp header line. The parser
(`parsing/getcounter.py`) extracts `\\machine\object(instance)\counter`
paths and their numeric values via regex; it does not depend on the
output locale. Multi-sample output (`-MaxSamples > 1`) is supported.

## test relog mock

Tests never invoke real `relog.exe`. `tests/conftest.py` provides a
`mock_relog` fixture that monkey-patches `parsing.relog._run_relog` to
copy a fixture CSV into the requested output path instead of shelling
out.

## Counter path normalization

Counter paths look like `\\HOSTNAME\Processor(_Total)\% Processor Time`.
For aggregation purposes we strip the leading `\\machine` so the same
counter from different hosts collapses to the same row in summary
tables. The full path is preserved as a column called `FullPath` for
audit.

## Per-queue regex

`get_per_queue_summary` uses a generic regex to find instances like
`RqNum_<N>`, `SqNum_<N>`, `Queue_<N>`, `Cpu_<N>`. This makes the tool
work for Mellanox `RqNum_*`, generic NIC `Queue_*`, and per-CPU
counters without hardcoding vendor strings.

## Evidence federation entity type

Per-log entity registration uses `counter_capture` as the entity_type,
mirroring etw-mcp's `register_entities_from_trace` shape but scoped to
the .blg lifecycle. Hostname is extracted from the counter paths
themselves (`\\HOSTNAME\...`) rather than a sysconfig text block, since
.blg files don't carry sysconfig.

## No orchestration coupling

The remote-friendly contract is enforced by `test_remote_zero_coupling.py`
which fails if any string `lablink` appears in `src/`. The string is
only allowed in docs and in tool output (e.g. as one of three example
transfer transports in `get_capture_instructions`).
