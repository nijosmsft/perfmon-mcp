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
