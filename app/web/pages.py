"""읽기 화면과 JSON API. 쓰기 작업은 admin.py (모두 CSRF 검사)."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    Briefing, Experiment, Holding, HoldingTransaction, Hypothesis, Instrument, LegacyRecord, OfficialPrice, Source,
)
from app.paper.ledger import events, replay, verify_chain
from app.paper.lifecycle import verified_summary
from app.providers.dart import DartProvider
from app.providers.naver import NaverQuoteProvider
from app.security import COOKIE_NAME, end_session
from app.services import evaluation, holdings as holdings_svc, macro_outlook, research
from app.services.briefings import KIND_LABEL, STATUS_LABEL, archive, latest_any, latest_published
from app.services.calendar import day_info, previous_trading_day
from app.services.market_data import latest_official, log_fetch, record_observation
from app.services.portability import export_compat
from app.services.research import ITEM_LABEL, ITEMS
from app.timeutil import today_kst, utcnow
from app.web.deps import page, require_csrf

router = APIRouter()

GLOSSARY = [
    ("KRX 정규장", "한국거래소에서 오전 9시~오후 3시 30분(기본값)에 열리는 정식 거래시간. 공식 종가는 이 시간의 마지막 가격입니다."),
    ("NXT", "넥스트레이드(대체거래소). 가격이 KRX와 다를 수 있어 이 서비스는 공식 평가에 쓰지 않습니다."),
    ("애프터마켓", "정규장이 끝난 뒤 거래. 정규장 종가와 섞지 않습니다."),
    ("수급", "외국인·기관·개인 등 투자자 집단이 순매수했는지 순매도했는지를 뜻합니다."),
    ("거래대금", "하루 동안 거래된 금액의 합. 유동성(사고팔기 쉬운 정도)을 가늠하는 지표입니다."),
    ("공시", "기업이 법에 따라 투자자에게 알리는 정보. DART(전자공시시스템)에 올라옵니다."),
    ("투자 가설", "'이 종목 가격이 언제까지 이 방향으로 움직일 것'이라는 검증 가능한 예측 기록입니다. 매수 권유가 아닙니다."),
    ("무효화 조건", "가설이 틀렸다고 인정할 사실 기준. 미리 정해 두고 나중에 바꾸지 않습니다."),
    ("적중률", "판정이 끝난 가설 중 방향이 맞은 비율. 보합·보류는 분모에서 빠집니다. 표본 20건 미만이면 '추적 중'입니다."),
    ("NAV", "모의 실험 계좌의 순자산가치(현금+보유 종목 평가액)."),
    ("bp", "베이시스 포인트. 1bp = 0.01%. 거래비용 15bp는 거래금액의 0.15%입니다."),
    ("출처 등급", "A 1차 공식자료 · B 출처 확인 가능한 데이터 제공처 · C 신뢰 가능한 언론·리서치 · D 미확인 정보."),
    ("모의운용", "가상의 돈으로 규칙에 따라 사고팔아 보는 실험. 실제 주문은 하지 않습니다."),
    ("해시 체인", "기록이 나중에 바뀌었는지 탐지하는 장치. 기록 내용이 사실임을 보증하지는 않습니다."),
]


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    end_session(db, request.cookies.get(COOKIE_NAME))
    db.commit()
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


@router.get("/", response_class=HTMLResponse)
def today_page(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    today = today_kst()
    info = day_info(db, today)
    pre = latest_published(db, "pre", today)
    pre_any = latest_any(db, "pre", today)
    close = latest_published(db, "close", today)
    close_any = latest_any(db, "close", today)
    last_close = latest_published(db, "close")
    return page(request, "today.html", today=today, info=info, pre=pre, pre_any=pre_any, close=close,
                close_any=close_any, last_close=last_close if (last_close and last_close != close) else None,
                kind_label=KIND_LABEL, status_label=STATUS_LABEL, sources=_source_map(db, [pre, pre_any, close,
                                                                                         close_any, last_close]))


def _source_map(db: Session, briefings: list[Briefing | None]) -> dict[int, Source]:
    ids = {f.get("source_id") for b in briefings if b for f in b.content.get("facts", []) if f.get("source_id")}
    return {s.id: s for s in db.scalars(select(Source).where(Source.id.in_(ids)))} if ids else {}


@router.get("/briefing/{bid}", response_class=HTMLResponse)
def briefing_detail(bid: int, request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    b = db.get(Briefing, bid)
    if b is None:
        raise HTTPException(404, "브리핑이 없습니다.")
    versions = db.scalars(select(Briefing).where(Briefing.trading_date == b.trading_date, Briefing.kind == b.kind)
                          .order_by(Briefing.version.desc())).all()
    return page(request, "briefing_detail.html", b=b, versions=versions, kind_label=KIND_LABEL,
                status_label=STATUS_LABEL, sources=_source_map(db, [b]))


@router.get("/archive", response_class=HTMLResponse)
def archive_page(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    items = archive(db, today_kst())
    legacy = db.scalars(select(LegacyRecord).where(LegacyRecord.kind == "briefing").order_by(LegacyRecord.id.desc())
                        .limit(100)).all()
    return page(request, "archive.html", items=items, legacy=legacy, kind_label=KIND_LABEL)


@router.get("/research", response_class=HTMLResponse)
def research_page(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    from app.models import ResearchVersion
    today = today_kst()
    cand = research.current_candidate(db)
    recent = db.scalars(select(ResearchVersion).order_by(ResearchVersion.id.desc()).limit(30)).all()
    hyps = db.scalars(select(Hypothesis).order_by(Hypothesis.id.desc())).all()
    stats = evaluation.statistics(db, today)
    legacy_rel = db.scalars(select(LegacyRecord).where(LegacyRecord.kind == "legacyRelative")).all()
    ev = {h.id: {hz: evaluation.current_event(db, h.id, hz) for hz in ("short", "medium")} for h in hyps}
    return page(request, "research.html", cand=cand, recent=recent, hyps=hyps, stats=stats, ev=ev,
                legacy_rel=legacy_rel, today=today, item_label=ITEM_LABEL)


@router.get("/research/{code}", response_class=HTMLResponse)
def company_page(code: str, request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    inst = db.get(Instrument, code)
    if inst is None:
        raise HTTPException(404, "종목 마스터에 없는 코드입니다.")
    hist = research.history_for_code(db, code)
    hyps = db.scalars(select(Hypothesis).where(Hypothesis.code == code).order_by(Hypothesis.id.desc())).all()
    ev = {h.id: {hz: evaluation.current_event(db, h.id, hz) for hz in ("short", "medium")} for h in hyps}
    src_ids = {s for r in hist for it in r.items.values() for s in it.get("source_ids", [])}
    src_ids |= {s for h in hyps for s in h.source_ids}
    sources = {s.id: s for s in db.scalars(select(Source).where(Source.id.in_(src_ids)))} if src_ids else {}
    return page(request, "company.html", inst=inst, hist=hist, hyps=hyps, ev=ev, items=ITEMS, sources=sources)


@router.get("/paper", response_class=HTMLResponse)
def paper_page(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    exps = db.scalars(select(Experiment).order_by(Experiment.id.desc())).all()
    return page(request, "paper.html", exps=exps, summary=verified_summary(db))


@router.get("/paper/{eid}", response_class=HTMLResponse)
def paper_detail(eid: int, request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    exp = db.get(Experiment, eid)
    if exp is None:
        raise HTTPException(404, "실험이 없습니다.")
    st = replay(db, exp)
    evs = events(db, exp.id)
    chain_ok, chain_msg = verify_chain(db, exp.id)
    marks = {}
    for p in st.held:
        row = latest_official(db, p.code, exp.end_date) if exp.end_date else None
        if row is None:
            from app.services.market_data import latest_official_on_or_before
            row = latest_official_on_or_before(db, p.code, today_kst())
        if row:
            marks[p.code] = row
    nav = st.nav({c: r.close for c, r in marks.items()}) if len(marks) == len(st.held) else None
    return page(request, "paper_detail.html", exp=exp, st=st, evs=evs, chain_ok=chain_ok, chain_msg=chain_msg,
                marks=marks, nav=nav)


@router.get("/holdings", response_class=HTMLResponse)
def holdings_page(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    today = today_kst()
    rows = []
    for h in db.scalars(select(Holding).order_by(Holding.code)):
        txs = db.scalars(select(HoldingTransaction).where(HoldingTransaction.holding_id == h.id)
                         .order_by(HoldingTransaction.id)).all()
        rows.append({"h": h, "pos": holdings_svc.position(db, h), "val": holdings_svc.valuation(db, h, today),
                     "txs": txs, "effective_ids": {t.id for t in holdings_svc.effective_transactions(db, h)}})
    return page(request, "holdings.html", rows=rows, today=today)


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", db: Session = Depends(get_db)):  # noqa: ANN201
    q = q.strip()[:40]
    results = []
    if getattr(request.state, "static_site", False):
        codes = recorded_codes(db)
        results = db.scalars(select(Instrument).where(Instrument.code.in_(codes)).order_by(Instrument.name)).all() \
            if codes else []
        return page(request, "search.html", q="", results=results, total=len(results))
    if q:
        like = f"%{q}%"
        results = db.scalars(select(Instrument).where(or_(Instrument.code == q, Instrument.name.like(like)))
                             .where(~Instrument.code.like("IDX:%")).order_by(Instrument.name).limit(50)).all()
    total = db.query(Instrument).count()
    return page(request, "search.html", q=q, results=results, total=total)


@router.get("/stock/{code}", response_class=HTMLResponse)
def stock_page(code: str, request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    inst = db.get(Instrument, code)
    if inst is None:
        raise HTTPException(404, "종목 마스터에 없는 코드입니다.")
    prices = db.scalars(select(OfficialPrice).where(OfficialPrice.code == code)
                        .order_by(OfficialPrice.trading_date, OfficialPrice.id)).all()
    latest_by_day = {}
    for p in prices:
        latest_by_day[p.trading_date] = p
    series = [latest_by_day[d] for d in sorted(latest_by_day)][-120:]
    gaps = []
    for a, b in zip(series, series[1:]):
        try:
            from app.services.calendar import trading_days_between
            missing = [d for d in trading_days_between(db, a.trading_date + timedelta(days=1),
                                                       b.trading_date - timedelta(days=1))]
            gaps += missing
        except Exception:  # noqa: BLE001
            pass
    disclosures, disc_status = [], None
    if getattr(request.state, "static_site", False):
        disc_status = "공개 사이트에는 공시 목록을 싣지 않습니다. 금융감독원 DART(dart.fss.or.kr)에서 직접 확인하세요."
    elif inst.is_etf:
        disc_status = "ETF는 기업 공시를 연결하지 않습니다."
    elif not inst.corp_code:
        disc_status = "DART 고유번호 매핑이 없습니다(관리자 화면에서 매핑 동기화 필요)."
    else:
        res = DartProvider().disclosures(inst.corp_code, today_kst() - timedelta(days=30), today_kst())
        log_fetch(db, "dart", f"list:{code}", res, record_count=len(res.data or []) if res.ok else 0)
        db.commit()
        if res.ok:
            disclosures = res.data
        else:
            disc_status = f"{res.status.value}: {res.detail}"
    return page(request, "stock.html", inst=inst, series=series, gaps=gaps, disclosures=disclosures,
                disc_status=disc_status, chart=_chart_svg(series))


def _chart_svg(series: list[OfficialPrice]) -> dict | None:
    if len(series) < 2:
        return None
    w, h, pad = 640, 200, 8
    closes = [p.close for p in series]
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1
    pts = []
    for i, p in enumerate(series):
        x = pad + i * (w - 2 * pad) / (len(series) - 1)
        y = pad + (hi - p.close) * (h - 2 * pad) / span
        pts.append(f"{x:.1f},{y:.1f}")
    vols = [p.volume or 0 for p in series]
    vmax = max(vols) or 1
    bars = []
    bw = (w - 2 * pad) / len(series)
    for i, v in enumerate(vols):
        bh = 50 * v / vmax
        bars.append({"x": round(pad + i * bw, 1), "y": round(60 - bh, 1), "w": round(max(bw - 1, 1), 1),
                     "h": round(bh, 1), "missing": series[i].volume is None})
    return {"w": w, "h": h, "points": " ".join(pts), "lo": lo, "hi": hi, "bars": bars, "first": series[0].trading_date,
            "last": series[-1].trading_date}


@router.get("/big-picture", response_class=HTMLResponse)
def big_picture(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    items = macro_outlook.list_outlooks(db)
    ids = {s for it in items for r in it["revisions"] for s in r.source_ids}
    sources = {s.id: s for s in db.scalars(select(Source).where(Source.id.in_(ids)))} if ids else {}
    return page(request, "big_picture.html", items=items, today=today_kst(), sources=sources,
                in_progress=macro_outlook.is_in_progress)


def recorded_codes(db: Session) -> list[str]:
    """기록(심층검증·가설·모의운용)이 있는 종목. 공개 사이트에 종목 화면을 만드는 범위."""
    from app.models import PaperEvent, ResearchVersion
    codes = {c for (c,) in db.execute(select(ResearchVersion.code).distinct())}
    codes |= {c for (c,) in db.execute(select(Hypothesis.code).distinct())}
    for (payload,) in db.execute(select(PaperEvent.payload)):
        if isinstance(payload, dict) and isinstance(payload.get("code"), str):
            codes.add(payload["code"])
    return sorted(c for c in codes if c and not c.startswith("IDX:"))


@router.get("/status", response_class=HTMLResponse)
def status_page(request: Request, db: Session = Depends(get_db)):  # noqa: ANN201
    from sqlalchemy import func

    from app.jobs import JOBS
    from app.models import JobRun
    from app.services.market_data import provider_status
    from app.services.system_status import overview
    today = today_kst()
    exps = db.scalars(select(Experiment).order_by(Experiment.id)).all()
    return page(request, "status.html", ov=overview(db), today=today, today_info=day_info(db, today),
                providers=provider_status(db), runs=db.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(30)).all(),
                job_labels={k: v.label for k, v in JOBS.items()},
                last_finished=db.scalar(select(func.max(JobRun.finished_at))),
                integrity={e.id: verify_chain(db, e.id) for e in exps})


@router.get("/glossary", response_class=HTMLResponse)
def glossary(request: Request):  # noqa: ANN201
    return page(request, "glossary.html", terms=GLOSSARY)


# ---------------------------------------------------------------- JSON API

@router.get("/api/export")
def api_export(db: Session = Depends(get_db)):  # noqa: ANN201
    return JSONResponse(export_compat(db))


@router.get("/api/status")
def api_status(db: Session = Depends(get_db)):  # noqa: ANN201
    from app.services.system_status import overview
    ov = overview(db)
    return {k: v for k, v in ov.items()}


@router.get("/api/quote/{code}")
def api_quote(code: str, db: Session = Depends(get_db)):  # noqa: ANN201
    """참고 시세(공식 평가 아님). 원본을 저장하고 적격성 판정과 함께 돌려준다."""
    if len(code) != 6 or not code.isalnum():
        raise HTTPException(400, "6자리 종목코드가 아닙니다.")
    provider = NaverQuoteProvider()
    req = utcnow()
    res = provider.fetch(code)
    obs = record_observation(db, code, res, req, utcnow())
    log_fetch(db, "naver", "quote", res, record_count=1 if res.ok else 0, raw_sha=obs.raw_sha256)
    db.commit()
    if not res.ok:
        return JSONResponse({"status": res.status.value, "detail": res.detail}, status_code=200)
    return {"status": "ok", "price": obs.price, "source_time": obs.source_time.isoformat() + "Z" if obs.source_time
            else None, "received_at": obs.received_at.isoformat() + "Z", "market_status": obs.market_status,
            "session_type": obs.session_type, "exchange": obs.exchange_type, "eligible_for_paper_fill": obs.eligible,
            "ineligible_reason": obs.ineligible_reason, "grade": "B", "note": "참고 시세 — 공식 평가는 KRX 종가만 사용"}
