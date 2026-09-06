from datetime import datetime, timezone

from freeup_space.models import Candidate, CandidateSnapshot, Risk
from freeup_space.policy import Policy
from freeup_space.revalidate import revalidate


def test_revalidate_rejects_modified_target(tmp_path):
    target = tmp_path / "candidate.bin"
    target.write_bytes(b"original")
    stat = target.lstat()
    snapshot = CandidateSnapshot(
        st_dev=stat.st_dev,
        st_ino=stat.st_ino,
        size=stat.st_size,
        mtime=stat.st_mtime,
        path=target,
        safe_root=tmp_path,
    )
    candidate = Candidate(
        candidate_id="OLD-001",
        path=target,
        category="old-file",
        reasons=("old file",),
        risk=Risk.MEDIUM,
        actionable=True,
        snapshot=snapshot,
        size=stat.st_size,
        reclaimable_bytes=stat.st_size,
        created_at=datetime.now(timezone.utc),
    )
    target.write_bytes(b"changed")

    decision = revalidate(
        candidate,
        Policy.for_platform("macos"),
    )

    assert decision.actionable is False
    assert decision.reason == "snapshot-mismatch"
