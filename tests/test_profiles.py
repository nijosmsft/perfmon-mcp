"""Profile metadata + .cset loader tests."""

from __future__ import annotations

import pytest

from perfmon_mcp.profiles.metadata import (
    PROFILES,
    extract_counter_paths,
    load_cset_text,
)


def test_four_profiles_registered():
    expected = {"system-overview", "cpu-detailed", "mellanox-rss", "mellanox-percpu"}
    assert set(PROFILES.keys()) == expected


def test_every_profile_has_required_metadata():
    for name, meta in PROFILES.items():
        assert meta.scenario == name, f"{name}: scenario mismatch"
        assert meta.cset_filename, f"{name}: missing cset_filename"
        assert meta.title, f"{name}: missing title"
        assert meta.when_to_use, f"{name}: missing when_to_use"
        assert meta.recommended_duration_s > 0


def test_mellanox_percpu_has_28pp_warning():
    """The overhead_notes block must call out the 28pp delivery cost
    pulled verbatim from the mellanox-rss-metrics skill - operators
    must see this before enabling the per-CPU profile in production.
    """
    meta = PROFILES["mellanox-percpu"]
    assert meta.overhead_notes, "mellanox-percpu must carry overhead_notes"
    text = meta.overhead_notes.lower()
    assert "28pp" in text or "28 pp" in text or "28 percentage" in text


def test_all_cset_files_load():
    for name in PROFILES:
        text = load_cset_text(name)
        assert text.strip(), f"{name}: empty .cset"
        paths = extract_counter_paths(text)
        assert paths, f"{name}: no counter paths in .cset"


def test_cset_paths_are_well_formed():
    for name in PROFILES:
        text = load_cset_text(name)
        for path in extract_counter_paths(text):
            assert path.startswith("\\"), f"{name}: {path!r} missing leading \\"
            assert "\\" in path[1:], f"{name}: {path!r} not a counter path"


def test_unknown_profile_raises():
    with pytest.raises(KeyError):
        load_cset_text("does-not-exist")


# ---------------------------------------------------------------------------
# v0.3: priority_metrics + default_instance_filter
# ---------------------------------------------------------------------------


def test_every_profile_has_v03_metadata_fields():
    """All four profiles must expose the v0.3 metadata fields, even
    when empty - the dataclass defaults guarantee it but we lock the
    contract here so the field rename surfaces immediately."""
    for name, meta in PROFILES.items():
        assert hasattr(meta, "priority_metrics"), f"{name}: missing priority_metrics"
        assert hasattr(meta, "default_instance_filter"), (
            f"{name}: missing default_instance_filter"
        )
        assert isinstance(meta.priority_metrics, list)
        assert isinstance(meta.default_instance_filter, str)


def test_mellanox_percpu_default_instance_filter_is_adapter_2():
    """mellanox-percpu was the only consumer of the hardcoded 'Adapter
    #2' string in v0.2; the profile metadata is now the single source
    of truth."""
    meta = PROFILES["mellanox-percpu"]
    assert meta.default_instance_filter == "Adapter #2"


def test_mellanox_percpu_priority_metrics_carries_curated_set():
    """The curated 8 hash-type counter names from the analyze-mellanox-rss
    skill must be on the profile so the network-lenses RSS distribution
    tool can fall back to them when no scenario_hint is given."""
    meta = PROFILES["mellanox-percpu"]
    assert len(meta.priority_metrics) >= 8
    joined = " ".join(meta.priority_metrics).lower()
    # At minimum the 4 IPv4 hash-type names + 4 IPv6 names should be present.
    assert "rss ipv4" in joined
    assert "rss ipv6" in joined


def test_mellanox_rss_has_ndis_poll_metric():
    """mellanox-rss is the cheap profile; its single priority metric
    is the NDIS poll-mode packet counter from the skill."""
    meta = PROFILES["mellanox-rss"]
    assert meta.priority_metrics, "mellanox-rss should carry at least one priority_metric"
    joined = " ".join(meta.priority_metrics).lower()
    assert "ndis poll" in joined or "packets processed" in joined


def test_non_mellanox_profiles_have_empty_v03_metadata():
    """system-overview / cpu-detailed don't have RSS-specific metadata;
    the v0.3 fields stay at their dataclass defaults for them."""
    for name in ("system-overview", "cpu-detailed"):
        meta = PROFILES[name]
        assert meta.priority_metrics == []
        assert meta.default_instance_filter == ""

