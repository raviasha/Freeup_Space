"""Groups select exact plan IDs across pages, never infer deletion eligibility."""
import time

import pytest

from freeup_space.widget_server import Server, TOOLS
from freeup_space.widget_service import WidgetService


def candidate(identifier, path, **overrides):
    return dict(candidate_id=identifier, path=path, category="old-file", risk="medium",
                reclaimable_bytes=100, reasons=[], actionable=True, proposed_action="trash",
                file_kind="regular", **overrides)


@pytest.fixture
def review(tmp_path):
    service = WidgetService()
    session = service.session(service.open(str(tmp_path))["session_id"])
    items = [candidate("VID-%d" % i, "/folder%d/Lesson_%03d.%s" % (i % 3, i, ("mp4", "MOV", "mkv")[i % 3]))
             for i in range(1205)]
    items += [dict(candidate("BLOCKED", "/managed/Lesson_1206.mp4"), actionable=False, risk="report-only"),
              candidate("AUDIO", "/folder/Lesson_000.mp3"),
              candidate("OTHER", "/folder/Unrelated_001.mp4"),
              dict(candidate("DIR", "/folder/Lesson_1207.mp4"), file_kind="directory")]
    session.update(status="review", run_id="groups", plan={"digest": "group-plan", "action": "trash", "candidates": items})
    yield service, session
    service.close()


def test_groups_cover_the_whole_plan_and_related_formats(review):
    service, session = review
    view = service.view(session, query="Lesson_001")
    assert len(view["candidates"]) == 1
    groups = view.get("similar_groups", [])
    assert len(groups) == 1, "Matching files should expose a whole-scan group"
    group = groups[0]
    assert group["count"] == 1206
    assert group["actionable_count"] == 1205
    assert group["bytes"] == 120500
    assert group["extensions"] == [".mkv", ".mov", ".mp4"]
    assert group["pattern"] == "lesson #"
    assert view["candidates"][0]["similar_group_id"] == group["group_id"]


def test_group_selection_ignores_visible_filters_and_keeps_other_choices(review):
    service, session = review
    service.save_review(session, ["OTHER"], "group-plan", query="Lesson_001")
    group = service.view(session)["similar_groups"][0]
    view = service.select_group(session, group["group_id"], "group-plan", "select")
    assert set(view["review_state"]["ids"]) == {"OTHER"} | {"VID-%d" % i for i in range(1205)}
    assert view["review_state"]["query"] == "Lesson_001"
    assert view["status"] == "review" and view["preview"] is None
    # Selecting twice cannot duplicate IDs; clearing only affects this group.
    service.select_group(session, group["group_id"], "group-plan", "select")
    cleared = service.select_group(session, group["group_id"], "group-plan", "clear")
    assert cleared["review_state"]["ids"] == ["OTHER"]


def test_group_selection_rejects_stale_unknown_and_inactive_requests(review):
    service, session = review
    group = service.view(session)["similar_groups"][0]["group_id"]
    for group_id, digest, action in [(group, "stale", "select"), ("unknown", "group-plan", "select"),
                                    (group, "group-plan", "invalid")]:
        with pytest.raises(ValueError):
            service.select_group(session, group_id, digest, action)
    session["closed"] = True
    with pytest.raises(ValueError):
        service.select_group(session, group, "group-plan", "select")
    session.update(closed=False, status="preview")
    with pytest.raises(ValueError):
        service.select_group(session, group, "group-plan", "select")
    assert session["review_state"] == {}


def test_name_matching_is_deterministic_across_paths_copies_and_unicode(tmp_path):
    service = WidgetService()
    session = service.session(service.open(str(tmp_path))["session_id"])
    names = ["Café Trip.mp4", "Cafe\u0301 Trip (1).MOV", "CAFÉ_TRIP - Copy 2.mkv", "Other Trip.mp4",
             "123.mp4", "456.mp4", "Notes.zip", "Notes.tar.gz", "Lesson01.mp4", "Lesson02.mov"]
    items = [candidate(str(i), ("C:\\Downloads\\" if i % 2 else "/Downloads/") + name) for i, name in enumerate(names)]
    session.update(status="review", plan={"digest": "names", "candidates": items})
    view = service.view(session)
    assert sorted(g["count"] for g in view["similar_groups"]) == [2, 2, 3]
    # Group identity and membership cannot depend on input ordering.
    session["plan"] = dict(session["plan"], candidates=list(reversed(items)))
    assert service.view(session)["similar_groups"] == view["similar_groups"]


def test_group_listing_and_members_are_paged(review):
    service, session = review
    group = service.view(session)["similar_groups"][0]["group_id"]
    view = service.view(session, offset=1200, similar_group=group)
    assert view["filtered_count"] == 1206
    assert len(view["candidates"]) == 6
    assert {c["candidate_id"] for c in view["candidates"]} == {"VID-1200", "VID-1201", "VID-1202", "VID-1203", "VID-1204", "BLOCKED"}
    items = [candidate("%d-%d" % (i, j), "/files/series%s_%d.zip" % (chr(65+i), j)) for i in range(25) for j in range(2)]
    session["plan"] = dict(session["plan"], candidates=items, digest="many-groups")
    first = service.view(session)
    second = service.view(session, group_offset=20)
    assert first["similar_group_count"] == 25
    assert len(first["similar_groups"]) == 20
    assert len(second["similar_groups"]) == 5
    assert not {g["group_id"] for g in first["similar_groups"]} & {g["group_id"] for g in second["similar_groups"]}


def test_bulk_tool_is_private_and_accepts_large_saved_selections(review):
    service, session = review
    server = Server()
    server.service = service
    tool = next(t for t in TOOLS if t["name"] == "select_similar_group")
    assert tool["_meta"]["ui"]["visibility"] == ["app"]
    assert "key" in tool["inputSchema"]["required"]
    args = dict(session_id=session["session_id"], key=session["key"], ids=["VID-%d" % i for i in range(1205)],
                plan_digest="group-plan", category="", query="", offset=0)
    view = server.call("save_review_state", args)["_meta"]["widget"]
    assert len(view["selected_candidates"]) == 1205
    with pytest.raises(ValueError):
        server.call("select_similar_group", dict(session_id=session["session_id"], key="wrong",
                    group_id=view["similar_groups"][0]["group_id"], plan_digest="group-plan", action="select"))


def test_large_group_previews_all_files_and_restores_after_restart(tmp_path):
    files = tmp_path / "Downloads"
    files.mkdir()
    for i in range(605):
        (files / ("course-%04d.zip" % i)).write_bytes(b"sample")
    service = WidgetService()
    session = service.session(service.open(str(tmp_path))["session_id"])
    service.start(session, "quick", "large-fixture", [str(files)])
    wait_for(session)
    assert session["status"] == "review", session["error"]
    group = service.view(session)["similar_groups"][0]["group_id"]
    chosen = service.select_group(session, group, session["plan"]["digest"], "select")
    ids = chosen["review_state"]["ids"]
    assert len(ids) == 605
    service.save_review(session, ids, session["plan"]["digest"], similar_group=group, offset=600)
    server = Server()
    server.service = service
    with pytest.raises(ValueError):
        server.call("preview_selection", dict(session_id=session["session_id"], key=session["key"],
                    plan_digest=session["plan"]["digest"], selection_digest="stale"))
    server.call("preview_selection", dict(session_id=session["session_id"], key=session["key"],
                selection_digest=session["review_state"]["selection_digest"], plan_digest=session["plan"]["digest"]))
    wait_for(session)
    assert session["status"] == "preview", session["error"]
    assert session["preview"]["ids"] == ids
    assert session["preview"]["blocked"] == []
    assert len(list(files.iterdir())) == 605
    service.close()
    restored = WidgetService()
    recovered = restored.session(session["session_id"], session["key"])
    wait_for(recovered)
    assert recovered["status"] == "review"
    assert len(recovered["selected_candidates"]) == 605
    assert recovered["review_state"]["similar_group"] == group
    assert recovered["review_state"]["offset"] == 600
    restored.close()


def test_incremental_edits_preserve_a_large_group_and_reject_stale_previews(review):
    service, session = review
    server = Server()
    server.service = service
    group = service.view(session)["similar_groups"][0]["group_id"]
    service.select_group(session, group, "group-plan", "select")
    previous = session["review_state"].get("selection_digest")
    view = server.call("save_review_state", dict(session_id=session["session_id"], key=session["key"],
        plan_digest="group-plan", category="", query="Lesson_001", offset=0,
        add_ids=["OTHER"], remove_ids=["VID-1000"]))["_meta"]["widget"]
    ids = view["review_state"]["ids"]
    assert len(ids) == 1205
    assert "OTHER" in ids and "VID-1000" not in ids and "VID-1204" in ids
    assert view["review_state"]["selection_digest"] != previous
    assert view["review_state"]["query"] == "Lesson_001"
    with pytest.raises(ValueError, match="selection changed"):
        service.preview(session, None, "group-plan", previous)


def test_large_saved_selection_and_receipt_can_be_read_again(tmp_path):
    service = WidgetService()
    session = service.session(service.open(str(tmp_path))["session_id"])
    ids = ["ARC-%03d" % i for i in range(160000)]
    session.update(run_id="large-recovery", review_state={"ids": ids, "plan_digest": "large"},
                   receipt={"moved": [{"candidate_id": i, "path": "/fixture/" + i + ".zip"} for i in ids]})
    service.store.save(session)
    assert service.store.load(session["session_id"])["review_state"]["ids"] == ids
    assert len(service.store.review(session["workspace"], "large-recovery")["receipt"]["moved"]) == 160000
    service.close()


def wait_for(session):
    deadline = time.monotonic() + 30
    while session["status"] in {"scanning", "loading", "previewing", "moving"}:
        assert time.monotonic() < deadline, session.get("error")
        time.sleep(.02)
