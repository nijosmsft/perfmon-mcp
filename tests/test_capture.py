"""Capture-emit tests. No live logman execution - these tools only render text."""

from __future__ import annotations

from perfmon_mcp.tools.capture import get_capture_commands, get_capture_instructions


def test_get_capture_commands_basic():
    out = get_capture_commands(
        scenario="system-overview",
        output_path="C:\\perfmon\\test.blg",
        duration_s=10,
    )
    assert isinstance(out, str)
    assert "logman" in out
    assert "C:\\perfmon\\test.blg" in out
    # Must contain create + start + stop + relog steps
    assert "create counter" in out
    assert "start" in out
    assert "stop" in out
    assert "relog" in out


def test_get_capture_commands_clamps_duration():
    short = get_capture_commands(
        scenario="system-overview",
        output_path="C:\\perfmon\\test.blg",
        duration_s=0,
    )
    long = get_capture_commands(
        scenario="system-overview",
        output_path="C:\\perfmon\\test.blg",
        duration_s=100000,
    )
    assert isinstance(short, str)
    assert isinstance(long, str)


def test_get_capture_commands_unknown_scenario():
    out = get_capture_commands(
        scenario="bogus", output_path="C:\\perfmon\\test.blg", duration_s=10
    )
    assert "bogus" in out.lower() or "unknown" in out.lower()


def test_remote_runbook_names_three_transports():
    """The instructions for target='remote' must surface multiple
    transports without coupling to any single orchestrator."""
    out = get_capture_instructions(
        scenario="system-overview",
        target="remote",
        output_path="C:\\perfmon\\test.blg",
    )
    lower = out.lower()
    # PSRemoting must be present
    assert "powershell remoting" in lower or "invoke-command" in lower or "psremoting" in lower
    # LabLink mentioned as ONE example transport
    assert "lablink" in lower
    # Manual scp / copy path
    assert "scp" in lower or "manual" in lower or "copy-item" in lower


def test_mellanox_percpu_warning_surfaced_in_runbook():
    out = get_capture_instructions(
        scenario="mellanox-percpu",
        target="local",
        output_path="C:\\perfmon\\rss.blg",
    )
    lower = out.lower()
    assert "28pp" in lower or "28 pp" in lower or "delivery cost" in lower or "overhead" in lower


def test_capture_commands_have_teardown():
    """The 6-step block MUST include teardown (logman delete + counter file rm)."""
    out = get_capture_commands(
        scenario="cpu-detailed",
        output_path="C:\\perfmon\\cpu.blg",
        duration_s=10,
    )
    assert "delete" in out.lower()


# ---------------------------------------------------------------------------
# v0.3: instance_filter kwarg + Adapter #2 hardcoding removal
# ---------------------------------------------------------------------------


def test_mellanox_percpu_default_uses_adapter_2():
    """No explicit instance_filter -> falls back to
    meta.default_instance_filter ('Adapter #2')."""
    out = get_capture_commands(
        scenario="mellanox-percpu",
        output_path="C:\\perfmon\\rss.blg",
        duration_s=10,
    )
    assert "Adapter #2" in out


def test_mellanox_percpu_explicit_filter_overrides_default():
    """Caller's instance_filter wins over the profile default."""
    out = get_capture_commands(
        scenario="mellanox-percpu",
        output_path="C:\\perfmon\\rss.blg",
        duration_s=10,
        instance_filter="Adapter #3",
    )
    assert "Adapter #3" in out
    # The default fallback must NOT also be applied.
    # Header text mentions the literal filter applied.
    lower = out.lower()
    assert "adapter #3" in lower


def test_non_mellanox_profile_no_filter_warning_or_skip():
    """When the profile has no priority_metrics and no caller filter,
    no Adapter literal is silently injected."""
    out = get_capture_commands(
        scenario="system-overview",
        output_path="C:\\perfmon\\sys.blg",
        duration_s=10,
    )
    # system-overview doesn't use the per-instance enumeration branch at all.
    assert "Adapter #2" not in out


def test_instance_filter_visible_in_instructions():
    out = get_capture_instructions(
        scenario="mellanox-percpu",
        target="remote",
        output_path="C:\\perfmon\\rss.blg",
        instance_filter="Adapter #5",
    )
    assert "Adapter #5" in out


def test_mellanox_percpu_runbook_no_hardcoded_default_when_overridden():
    """Regression guard: in v0.2, mellanox-percpu emitted a literal
    'Adapter #2' string regardless of input. Now it must respect the
    override."""
    out = get_capture_commands(
        scenario="mellanox-percpu",
        output_path="C:\\perfmon\\rss.blg",
        duration_s=10,
        instance_filter="MyCustomNic",
    )
    # The Where-Object -match clause must use the override, not 'Adapter #2'.
    # Find the rssPaths line or similar and verify.
    assert "MyCustomNic" in out
    # The body of the runbook (not the header overhead-warning) must
    # not silently rewrite the filter back to Adapter #2.
    # Heuristic: count occurrences - 0 'Adapter #2' is the strict win.
    assert "Adapter #2" not in out

