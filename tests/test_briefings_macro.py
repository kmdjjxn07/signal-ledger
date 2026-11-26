from datetime import date

import pytest

from app.models import Briefing, LedgerViolation
from app.services.briefings import (
    BriefingError, archive, empty_content, ensure_scheduled_close, latest_any, latest_published, publish_draft,
    run_close_brief, run_pre_brief, save_version,
)
from app.services.macro_outlook import add_revision, list_outlooks, quarter_period
from tests.conftest import add_price, add_source

D = date(2026, 11, 24)
PREV = date(2026, 11, 23)


def test_pre_brief_publishes_facts_and_creates_scheduled_close(calendar_2026):
    db = calendar_2026
    add_price(db, "IDX:KOSPI", PREV, 265032, scale=100)
    add_price(db, "IDX:KOSDAQ", PREV, 84510, scale=100)
    out = run_pre_brief(db, D)
    db.commit()
    assert out["result"] == "published"
    pre = latest_published(db, "pre", D)
    assert len(pre.content["facts"]) == 2 and pre.content["facts"][0]["grade"] == "A"
    assert any(s["status"] == "not_configured" for s in pre.content["data_status"])  # AI·FRED 미연결 표시
    close = latest_any(db, "close", D)
    assert close.status == "scheduled" and close.content["summary"] == "오늘 장 마감 후 발행 예정"


def test_pre_brief_without_facts_is_withheld(calendar_2026):
    db = calendar_2026
    assert run_pre_brief(db, D)["result"] == "withheld"
    assert latest_published(db, "pre", D) is None


def test_delayed_pre_job_does_not_reset_published_close(calendar_2026):
    db = calendar_2026
    src = add_source(db, "A")
    save_version(db, D, "close", "published", {**empty_content(), "facts": [{"text": "x", "source_id": src.id}]},
                 "owner:t")
    assert ensure_scheduled_close(db, D, "job:pre") is None
    assert latest_any(db, "close", D).status == "published"


def test_close_brief_withheld_without_official_and_published_with(calendar_2026):
    db = calendar_2026
    assert run_close_brief(db, D)["result"] == "withheld"
    add_price(db, "IDX:KOSPI", D, 265032, scale=100)
    add_price(db, "IDX:KOSDAQ", D, 84510, scale=100)
    out = run_close_brief(db, D)
    assert out["result"] == "published"
    b = db.get(Briefing, out["briefing_id"])
    flow = [s for s in b.content["data_status"] if "수급" in s["item"]][0]
    assert flow["status"] == "not_configured"  # 수급은 가짜 값 대신 연결 필요로 표시


def test_close_brief_skips_holiday_without_copying(calendar_2026):
    db = calendar_2026
    add_price(db, "IDX:KOSPI", D, 265032, scale=100)
    assert run_close_brief(db, date(2026, 12, 25))["result"] == "skipped"
    assert latest_any(db, "close", date(2026, 12, 25)) is None


def test_archive_keeps_versions_and_publish_validation(calendar_2026):
    db = calendar_2026
    src_a, src_d = add_source(db, "A"), add_source(db, "D")
    bad = save_version(db, PREV, "pre", "draft", {**empty_content(), "facts": [{"text": "소문", "source_id": src_d.id}]},
                       "owner:t")
    with pytest.raises(BriefingError):
        publish_draft(db, bad.id, "owner:t")
    good = save_version(db, PREV, "pre", "draft", {**empty_content(), "facts": [{"text": "공시", "source_id": src_a.id}]},
                        "owner:t")
    pub = publish_draft(db, good.id, "owner:t")
    assert pub.version == 3 and db.get(Briefing, good.id).status == "draft"  # 초안 보존
    assert [b.id for b in archive(db, D)] == [pub.id]


def test_macro_revisions_append_and_quarter():
    assert quarter_period("2026Q4") == (date(2026, 10, 1), date(2026, 12, 31))
    with pytest.raises(ValueError):
        quarter_period("2026-4")


def test_macro_revision_requires_sources_and_preserves_history(db):
    src = add_source(db, "A")
    content = {"facts": "f", "interpretation": "i", "risks": "r", "next_checks": "n"}
    with pytest.raises(ValueError):
        add_revision(db, "2026Q4", data_as_of=D, reason="x", content=content, source_ids=[], actor="t")
    add_revision(db, "2026Q4", data_as_of=D, reason="최초", content=content, source_ids=[src.id], actor="t")
    add_revision(db, "2026Q4", data_as_of=D, reason="물가 발표", content={**content, "facts": "f2"}, source_ids=[src.id],
                 actor="t")
    db.commit()
    item = list_outlooks(db)[0]
    assert [r.revision_no for r in item["revisions"]] == [2, 1]
    old = item["revisions"][1]
    old.reason = "수정 시도"
    with pytest.raises(LedgerViolation):
        db.flush()
