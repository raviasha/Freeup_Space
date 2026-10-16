"""A suggested older-version removal requires its unselected latest keeper."""
from dataclasses import replace
from datetime import datetime, timezone
import os

import pytest

from freeup_space import apply
from freeup_space.models import Candidate, CandidateSnapshot, CleanupPlan, Risk
from freeup_space.policy import Policy
from freeup_space.revalidate import revalidate


NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
PLATFORM = "windows" if os.name == "nt" else "macos"


def candidate(path, identifier):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(identifier.encode() + b" synthetic revision contents")
    snapshot = CandidateSnapshot.from_path(path)
    return Candidate(candidate_id=identifier, path=path, category="personal-data",
                     reasons=("valuable user data",), risk=Risk.HIGH, actionable=True,
                     snapshot=snapshot, size=snapshot.size, file_kind="regular",
                     evidence={"categories": ("personal-data",)},
                     reclaimable_bytes=snapshot.size, created_at=NOW)


def plan_for(*candidates):
    return CleanupPlan(schema_version="1", run_id="version-test", platform=PLATFORM, created_at=NOW,
                       policy_version="1", candidates=candidates, digest="a" * 64)


@pytest.fixture
def revisions(tmp_path):
    return plan_for(candidate(tmp_path / "Proposal_v1.docx", "VER-1"),
                    candidate(tmp_path / "Proposal_v2.docx", "VER-2"),
                    candidate(tmp_path / "Proposal_v10.docx", "VER-10"))


@pytest.fixture
def movement_boundary(monkeypatch):
    moved = []
    monkeypatch.setattr(apply, "prepare_macos_trash", lambda *args: None)

    def move(path, platform, size):
        moved.append(path)
        return apply.MoveResult(path, True, size, "synthetic-no-move")

    monkeypatch.setattr(apply, "_move", move)
    return moved


def break_keeper(path, change):
    if change == "missing":
        path.unlink()
    elif change == "replaced":
        replacement = path.with_name("replacement.tmp")
        replacement.write_bytes(b"a different document")
        replacement.replace(path)
    else:
        target = path.with_name("symlink-target.docx")
        target.write_bytes(b"a different document")
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                pytest.skip("Windows symlink privilege unavailable")
            raise


@pytest.mark.parametrize("change", ["missing", "replaced", "symlink"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_changed_latest_keeper_blocks_older_revision(revisions, movement_boundary, change, dry_run):
    # Omitting keeper revalidation would report dry-run or move the older file.
    older, _, latest = revisions.candidates
    break_keeper(latest.path, change)
    receipt = apply.apply_selection(revisions, [older.candidate_id], PLATFORM, dry_run=dry_run)
    assert receipt.moved == () and receipt.failed == ()
    assert len(receipt.skipped) == 1
    assert receipt.skipped[0].reason == "latest version keeper changed or is unavailable"
    assert movement_boundary == []
    assert older.path.is_file()


@pytest.mark.parametrize("dry_run", [True, False])
def test_intact_unique_highest_revision_permits_older_revision(revisions, movement_boundary, dry_run):
    older, _, latest = revisions.candidates
    receipt = apply.apply_selection(revisions, [older.candidate_id], PLATFORM, dry_run=dry_run)
    assert receipt.failed == ()
    if dry_run:
        assert receipt.skipped[0].reason == "dry-run"
        assert movement_boundary == []
    else:
        assert receipt.skipped == ()
        assert [item.path for item in receipt.moved] == [older.path]
        assert movement_boundary == [older.path]
    assert latest.path.is_file()
    assert receipt.plan_digest == revisions.digest


def test_selected_all_versions_remains_an_intentional_selection(revisions, movement_boundary):
    older, middle, latest = revisions.candidates
    latest.path.unlink()
    receipt = apply.apply_selection(revisions, [item.candidate_id for item in revisions.candidates], PLATFORM)
    assert [item.path for item in receipt.moved] == [older.path, middle.path]
    assert receipt.skipped[0].candidate_id == latest.candidate_id
    assert receipt.skipped[0].reason == "target is missing"
    assert movement_boundary == [older.path, middle.path]


@pytest.mark.skipif(os.name == "nt", reason="macOS preparation stage")
def test_macos_preparation_rechecks_the_latest_keeper(revisions, movement_boundary, monkeypatch):
    older, _, latest = revisions.candidates
    monkeypatch.setattr(apply, "prepare_macos_trash", lambda *args: latest.path.unlink())
    receipt = apply.apply_selection(revisions, [older.candidate_id], PLATFORM)
    assert receipt.moved == () and receipt.failed == ()
    assert receipt.skipped[0].reason == "latest version keeper changed or is unavailable"
    assert movement_boundary == []


@pytest.mark.parametrize("other", ["Other_v2.docx", "other/Proposal_v2.docx", "Proposal_v2.pptx"])
def test_version_guard_never_crosses_families(tmp_path, movement_boundary, other):
    older = candidate(tmp_path / "Proposal_v1.docx", "OLD")
    unrelated = candidate(tmp_path / other, "OTHER")
    unrelated.path.unlink()
    receipt = apply.apply_selection(plan_for(older, unrelated), ["OLD"], PLATFORM, dry_run=True)
    assert receipt.skipped[0].reason == "dry-run"


def test_ambiguous_highest_revision_does_not_add_a_keeper_dependency(tmp_path, movement_boundary):
    older = candidate(tmp_path / "Proposal_v1.docx", "OLD")
    highest = candidate(tmp_path / "Proposal_v2.docx", "LATEST")
    tied = candidate(tmp_path / "Proposal_rev2.docx", "TIED")
    highest.path.unlink()
    receipt = apply.apply_selection(plan_for(older, highest, tied), ["OLD"], PLATFORM, dry_run=True)
    assert receipt.skipped[0].reason == "dry-run"


def test_keeper_check_uses_metadata_without_version_content_hashing(revisions, monkeypatch):
    older, _, latest = revisions.candidates
    latest = replace(latest, snapshot=replace(latest.snapshot, digest="b" * 64))
    monkeypatch.setattr("freeup_space.safety._digest", lambda *args: pytest.fail("version keeper contents were hashed"))
    decision = revalidate(older, Policy.for_platform(PLATFORM), version_keeper=latest)
    assert decision.actionable


def test_keeper_removed_after_successful_preview_blocks_actual_apply(revisions, movement_boundary):
    older, _, latest = revisions.candidates
    preview = apply.apply_selection(revisions, [older.candidate_id], PLATFORM, dry_run=True)
    assert preview.skipped[0].reason == "dry-run"
    latest.path.unlink()
    receipt = apply.apply_selection(revisions, [older.candidate_id], PLATFORM)
    assert receipt.moved == () and receipt.failed == ()
    assert receipt.skipped[0].reason == "latest version keeper changed or is unavailable"
    assert movement_boundary == []
    assert older.path.is_file()
