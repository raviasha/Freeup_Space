"""Recommendations keep their promises even across hidden pages and overlaps."""
from datetime import datetime, timezone

import pytest

from freeup_space.widget_service import WidgetService
from freeup_space.widget_server import Server, TOOLS


NOW = datetime(2026, 10, 15, tzinfo=timezone.utc).timestamp()
OLD = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()


def file(identifier, name, *, modified=OLD, category="personal-data", **extra):
    return dict(candidate_id=identifier, path=name, category=category, risk="high",
                reclaimable_bytes=100, reasons=[], actionable=True, proposed_action="trash",
                file_kind="regular", snapshot={"mtime": modified}, evidence={}, **extra)


def session_for(tmp_path, items):
    service = WidgetService()
    session = service.session(service.open(str(tmp_path))["session_id"])
    session.update(status="review", run_id="recommendations", plan={"digest": "plan-1",
        "platform": "macos", "created_at": "2026-10-15T00:00:00+00:00", "candidates": items})
    return service, session


def test_versions_keep_latest_numeric_revision_and_separate_formats_and_projects(tmp_path):
    items = [file("V1", "/work/Proposal_v1.docx"), file("V2", "/work/Proposal_v2.docx"),
             file("V10", "/work/Proposal_v10.docx"), file("PDF", "/work/Proposal_v2.pdf"),
             file("OTHER", "/other/Proposal_v3.docx"), file("PHOTO1", "/work/Holiday_001.jpg"),
             file("PHOTO2", "/work/Holiday_002.jpg")]
    service, session = session_for(tmp_path, items)
    view = service.view(session, category="review:older-versions")
    assert view.get("version_group_count") == 1, "Explicit revisions should expose a keep-latest group"
    group = view["version_groups"][0]
    assert group["keep_paths"] == ["/work/Proposal_v10.docx"]
    assert group["recommended_count"] == 2 and group["recommended_bytes"] == 200
    selected = service.select_recommended(session, group["category"], "plan-1", "select")
    assert selected["review_state"]["ids"] == ["V1", "V2"]
    assert selected["status"] == "review" and selected["preview"] is None


def test_conflicting_dates_and_tied_latest_versions_do_not_get_cleanup_recommendations(tmp_path):
    items = [file("A1", "/a/Draft_v1.docx", modified=NOW-10), file("A2", "/a/Draft_v2.docx"),
             file("B1", "/b/Draft_v1.docx"), file("B2", "/b/Draft_v2.docx"),
             file("B20", "/b/Draft_v2.0.docx")]
    service, session = session_for(tmp_path, items)
    groups = service.view(session, category="review:older-versions")["version_groups"]
    assert len(groups) == 2
    assert all(g["recommended_count"] == 0 for g in groups)
    assert all(g["note"] for g in groups)


def test_recommended_selection_covers_hidden_pages_and_preserves_other_choices(tmp_path):
    items = [file("%d-%d" % (family, version), "/work/Project%d_v%d.docx" % (family, version))
             for family in range(70) for version in (1, 2, 3)]
    items.append(file("OTHER", "/work/Unrelated.zip", category="archive"))
    service, session = session_for(tmp_path, items)
    service.save_review(session, ["OTHER"], "plan-1", category="review:older-versions", query="Project0_")
    view = service.select_recommended(session, "review:older-versions", "plan-1", "select")
    assert set(view["review_state"]["ids"]) == {"OTHER"} | {"%d-%d" % (f, v) for f in range(70) for v in (1, 2)}
    assert view["review_state"]["query"] == "Project0_"
    cleared = service.select_recommended(session, "review:older-versions", "plan-1", "clear")
    assert cleared["review_state"]["ids"] == ["OTHER"]


def test_recommendations_never_select_protected_files_or_a_promised_keeper(tmp_path):
    items = [dict(file("OLD", "/work/Draft_v1.docx"), risk="report-only", actionable=False),
             file("NEW", "/work/Draft_v2.docx"), file("A", "/work/Other_v1.docx"),
             file("B", "/work/Other_v2.docx")]
    service, session = session_for(tmp_path, items)
    service.save_review(session, ["B"], "plan-1")
    with pytest.raises(ValueError, match="Keep"):
        service.select_recommended(session, "review:older-versions", "plan-1", "select")
    assert session["review_state"]["ids"] == ["B"]
    service.save_review(session, [], "plan-1")
    view = service.select_recommended(session, "review:older-versions", "plan-1", "select")
    assert view["review_state"]["ids"] == ["A"]


def test_opening_age_is_distinct_from_modification_and_missing_history(tmp_path):
    items = [file("OPEN_OLD", "/work/Old.pdf"), file("OPEN_NEW", "/work/Read.pdf"),
             file("UNKNOWN", "/work/Unknown.pdf"), file("FUTURE", "/work/Future.pdf"),
             file("ACCESS", "/work/Access.pdf"), file("EDITED", "/work/Edited.pdf", modified=NOW-10)]
    for item, opened, source in zip(items, [OLD, NOW-10, None, NOW+10, OLD, OLD],
                                   ["macos-launchservices"] * 4 + ["filesystem-atime", "macos-launchservices"]):
        item["evidence"]["opening_history"] = {"last_opened_at": opened, "source": source}
    service, session = session_for(tmp_path, items)
    view = service.view(session, category="review:not-opened:12")
    assert [c["candidate_id"] for c in view["candidates"]] == ["OPEN_OLD"]
    assert view["opening_history_unknown_count"] == 3
    modified = service.view(session, category="review:not-modified:12")
    assert {c["candidate_id"] for c in modified["candidates"]} == {"UNKNOWN", "FUTURE", "ACCESS"}
    assert all(c["review_details"]["opening_history_label"] == "Opening history unavailable" for c in modified["candidates"])


def test_age_filters_use_calendar_months_and_saved_scan_time(tmp_path):
    item = file("MID", "/work/Notes.pdf", modified=datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp())
    item["evidence"]["opening_history"] = {"last_opened_at": item["snapshot"]["mtime"], "source": "macos-launchservices"}
    service, session = session_for(tmp_path, [item])
    assert service.view(session, category="review:not-opened:6")["filtered_count"] == 1
    assert service.view(session, category="review:not-opened:12")["filtered_count"] == 0
    assert service.view(session, category="review:not-opened:24")["filtered_count"] == 0


def test_recommendation_tool_rejects_stale_and_inactive_selection(tmp_path):
    service, session = session_for(tmp_path, [file("A", "/work/Draft_v1.docx"), file("B", "/work/Draft_v2.docx")])
    for category, digest, action in [("review:older-versions", "stale", "select"),
                                    ("bad", "plan-1", "select"), ("review:older-versions", "plan-1", "bad")]:
        with pytest.raises(ValueError):
            service.select_recommended(session, category, digest, action)
    session["status"] = "preview"
    with pytest.raises(ValueError):
        service.select_recommended(session, "review:older-versions", "plan-1", "select")
    spec = next(t for t in TOOLS if t["name"] == "select_recommended")
    assert spec["_meta"]["ui"]["visibility"] == ["app"]
    server = Server()
    server.service.close()
    server.service = service
    result = server.dispatch({"method": "tools/call", "params": {"name": "select_recommended", "arguments": {
        "session_id": session["session_id"], "key": "wrong", "plan_digest": "plan-1",
        "category": "review:older-versions", "action": "select"}}})
    assert result["isError"]


def test_disposable_recommendations_require_evidence_and_age_while_large_files_only_sort(tmp_path):
    items = [dict(file("CACHE", "/cache/old.bin", category="cache"), risk="low"),
             dict(file("RECENT", "/cache/new.bin", category="cache", modified=NOW-60), risk="low"),
             dict(file("MANAGED", "/managed/log.bin", category="log"), risk="report-only", actionable=False),
             file("PERSONAL", "/cache/photo.jpg", category="cache"),
             dict(file("INSTALLER", "/downloads/old.pkg", category="installer"), risk="medium"),
             file("DOWNLOAD_DOC", "/downloads/old.pdf", category="download"),
             dict(file("BIG", "/work/Movie.mp4"), reclaimable_bytes=200*1024*1024),
             dict(file("BIGGER", "/work/Film.mov"), reclaimable_bytes=300*1024*1024)]
    service, session = session_for(tmp_path, items)
    assert service.select_recommended(session, "review:clutter", "plan-1", "select")["review_state"]["ids"] == ["CACHE"]
    assert service.select_recommended(session, "review:old-downloads", "plan-1", "select")["review_state"]["ids"] == ["CACHE", "INSTALLER"]
    view = service.view(session, category="review:large-personal")
    assert [c["candidate_id"] for c in view["candidates"]] == ["BIGGER", "BIG"]
    assert view["active_recommendation"]["recommended_count"] == 0


def test_retained_copy_stays_reserved_across_overlapping_version_and_age_categories(tmp_path):
    items = [file("V1", "/work/Draft_v1.docx"), file("V2", "/work/Draft_v2.docx"),
             file("COPY", "/backup/Draft_copy.docx")]
    # A saved older plan might retain v1 as its verified original. Do not contradict it.
    items[2]["evidence"]["retained_path"] = items[0]["path"]
    items[2]["snapshot"]["digest"] = "a" * 64
    service, session = session_for(tmp_path, items)
    assert service.view(session, category="review:older-versions")["active_recommendation"]["recommended_count"] == 0
    assert service.select_recommended(session, "review:extra-copies", "plan-1", "select")["review_state"]["ids"] == ["COPY"]
    view = service.select_recommended(session, "review:not-modified:12", "plan-1", "select")
    assert view["review_state"]["ids"] == ["COPY"]


def test_malformed_huge_timestamp_does_not_break_saved_scan_review(tmp_path):
    item = file("HUGE", "/work/Draft_v1.docx", modified=10**400)
    item["evidence"]["opening_history"] = {"last_opened_at": 10**400, "source": "macos-launchservices"}
    service, session = session_for(tmp_path, [item])
    assert service.view(session)["opening_history_unknown_count"] == 1


def test_macos_literal_backslash_does_not_merge_different_folders(tmp_path):
    items = [file("NESTED", "/work/Proposal_v1.docx"), file("LITERAL", r"/work\Proposal_v2.docx")]
    service, session = session_for(tmp_path, items)
    assert service.view(session)["version_group_count"] == 0


def test_windows_version_families_normalize_folder_case_and_separators(tmp_path):
    items = [file("V1", r"C:\Work\Proposal_v1.docx"), file("V2", "c:/work/Proposal_v2.docx")]
    service, session = session_for(tmp_path, items)
    session["plan"]["platform"] = "windows"
    assert service.view(session)["version_group_count"] == 1
    assert service.select_recommended(session, "review:older-versions", "plan-1", "select")["review_state"]["ids"] == ["V1"]


def test_regular_pages_documents_get_keep_latest_recommendations(tmp_path):
    items = [file("V1", "/work/Proposal_v1.pages"), file("V2", "/work/Proposal_v2.pages")]
    service, session = session_for(tmp_path, items)
    assert service.view(session)["version_group_count"] == 1
    assert service.select_recommended(session, "review:older-versions", "plan-1", "select")["review_state"]["ids"] == ["V1"]
