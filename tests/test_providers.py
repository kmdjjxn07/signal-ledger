import io
import zipfile
from datetime import date

import httpx

from app.providers.base import PStatus, _strip_secret_params
from app.providers.dart import DartProvider
from app.providers.krx import KrxDailyProvider
from app.providers.naver import NaverQuoteProvider, validate_quote
from app.services.settings_store import DEFAULTS
from tests.conftest import add_instrument, json_response, naver_payload

D = date(2026, 11, 24)
STOCK_ROW = {"BAS_DD": "20261124", "ISU_CD": "005930", "ISU_NM": "테스트전자", "TDD_CLSPRC": "70,000",
             "TDD_OPNPRC": "69,000", "TDD_HGPRC": "71,000", "TDD_LWPRC": "68,500", "ACC_TRDVOL": "1,234",
             "ACC_TRDVAL": "86,380,000", "MKTCAP": "1,000,000,000"}


def test_krx_not_configured_without_key():
    assert KrxDailyProvider(api_key="").stock_daily("KOSPI", D).status == PStatus.NOT_CONFIGURED


def test_krx_distinguishes_empty_auth_forbidden_network(mock_http):
    cases = {
        "empty": (lambda r: json_response({"OutBlock_1": []}), PStatus.EMPTY),
        "auth": (lambda r: httpx.Response(401), PStatus.AUTH_FAILED),
        "forbidden": (lambda r: httpx.Response(403), PStatus.FORBIDDEN),
        "redirect": (lambda r: httpx.Response(302, headers={"location": "https://evil.test/"}), PStatus.INVALID),
    }
    for name, (handler, expected) in cases.items():
        res = KrxDailyProvider(http=mock_http(handler), api_key="k").stock_daily("KOSPI", D)
        assert res.status == expected, name

    def boom(r):
        raise httpx.ConnectError("down")
    assert KrxDailyProvider(http=mock_http(boom), api_key="k").stock_daily("KOSPI", D).status == PStatus.NETWORK_ERROR


def test_krx_parses_rows_and_rejects_wrong_date(mock_http):
    other = {**STOCK_ROW, "ISU_CD": "000660", "BAS_DD": "20261123"}
    res = KrxDailyProvider(http=mock_http(lambda r: json_response({"OutBlock_1": [STOCK_ROW, other]})),
                           api_key="k").stock_daily("KOSPI", D)
    assert res.ok and len(res.data) == 1 and res.data[0].close == 70000 and res.data[0].volume == 1234
    assert "제외" in res.detail


def test_krx_index_scaled(mock_http):
    row = {"BAS_DD": "20261124", "IDX_NM": "코스피", "CLSPRC_IDX": "2,650.32", "ACC_TRDVOL": "100", "ACC_TRDVAL": "200"}
    res = KrxDailyProvider(http=mock_http(lambda r: json_response({"OutBlock_1": [row]})), api_key="k").index_daily("KOSPI", D)
    assert res.ok and res.data.close == 265032 and res.data.scale == 100


def test_secret_params_stripped_from_logged_url():
    assert "crtfc_key" not in _strip_secret_params("https://x.test/a?crtfc_key=SECRET&corp_code=1")


def test_dart_corp_codes_and_other_company_filtered(mock_http):
    xml = ("<result><list><corp_code>00126380</corp_code><corp_name>테스트전자</corp_name><stock_code>005930</stock_code></list>"
           "<list><corp_code>99999999</corp_code><corp_name>비상장</corp_name><stock_code> </stock_code></list></result>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", xml)
    res = DartProvider(http=mock_http(lambda r: httpx.Response(200, content=buf.getvalue())), api_key="k").corp_codes()
    assert res.ok and res.data == {"005930": {"corp_code": "00126380", "corp_name": "테스트전자"}}

    payload = {"status": "000", "list": [
        {"corp_code": "00126380", "rcept_no": "1", "report_nm": "주요사항", "rcept_dt": "20261124"},
        {"corp_code": "11111111", "rcept_no": "2", "report_nm": "다른 회사", "rcept_dt": "20261124"}]}
    res = DartProvider(http=mock_http(lambda r: json_response(payload)), api_key="k").disclosures("00126380", D, D)
    assert [x["rcept_no"] for x in res.data] == ["1"]
    res = DartProvider(http=mock_http(lambda r: json_response({"status": "013", "message": "없음"})), api_key="k") \
        .disclosures("00126380", D, D)
    assert res.status == PStatus.EMPTY
    res = DartProvider(http=mock_http(lambda r: json_response({"status": "010"})), api_key="k").disclosures("00126380", D, D)
    assert res.status == PStatus.AUTH_FAILED


def test_naver_validation_rules():
    rules = {**DEFAULTS["naver_validation"], "confirmed_by_owner": True}
    ok = validate_quote("005930", naver_payload(), rules)
    assert ok.eligible and ok.price == 70000  # overMarketPriceInfo(99,999)는 가격으로 쓰지 않음
    assert not validate_quote("005930", naver_payload(session="AFTER_MARKET"), rules).eligible
    assert not validate_quote("005930", naver_payload(status="CLOSE"), rules).eligible
    assert not validate_quote("005930", naver_payload(t="2026-11-24T10:00:30"), rules).eligible  # 시간대 없음
    assert not validate_quote("000660", naver_payload(), rules).eligible  # 코드 불일치
    assert not validate_quote("005930", naver_payload(exch="NXT"), rules).eligible
    no_session = naver_payload()
    del no_session["datas"][0]["marketSessionType"]
    assert "세션" in validate_quote("005930", no_session, rules).reason
    unconfirmed = validate_quote("005930", naver_payload(), DEFAULTS["naver_validation"])
    assert not unconfirmed.eligible and "확정" in unconfirmed.reason


def test_naver_disabled_by_default():
    assert NaverQuoteProvider(enabled=False).fetch("005930").status == PStatus.NOT_CONFIGURED


def test_official_sync_dedupe_correction_and_failure_preserves(calendar_2026, mock_http):
    from app.models import FetchLog, OfficialPrice
    from app.services.market_data import latest_official, sync_official_daily
    db = calendar_2026
    rows = {"OutBlock_1": [STOCK_ROW]}
    idx = {"OutBlock_1": [{"BAS_DD": "20261124", "IDX_NM": "코스피", "CLSPRC_IDX": "2,650.32"},
                          {"BAS_DD": "20261124", "IDX_NM": "코스닥", "CLSPRC_IDX": "845.10"}]}

    def handler(r):
        return json_response(idx if "/idx/" in r.url.path else rows)
    prov = KrxDailyProvider(http=mock_http(handler), api_key="k")
    sync_official_daily(db, D, prov)
    sync_official_daily(db, D, prov)  # 같은 내용 → 중복 저장 안 함
    db.commit()
    assert db.query(OfficialPrice).filter_by(code="005930").count() == 1
    rows["OutBlock_1"] = [{**STOCK_ROW, "TDD_CLSPRC": "70,100"}]  # 정정
    sync_official_daily(db, D, prov)
    db.commit()
    latest = latest_official(db, "005930", D)
    assert latest.close == 70100 and latest.supersedes_id is not None
    fail = KrxDailyProvider(http=mock_http(lambda r: httpx.Response(401)), api_key="k")
    sync_official_daily(db, D, fail)
    db.commit()
    assert latest_official(db, "005930", D).close == 70100  # 실패가 성공값을 덮지 않음
    assert db.query(FetchLog).filter_by(status="auth_failed").count() >= 1
    # 휴장일과 미발행 구분
    sync_official_daily(db, date(2026, 12, 25), prov)
    assert db.query(FetchLog).filter_by(status="closed").count() == 1
    empty = KrxDailyProvider(http=mock_http(lambda r: json_response({"OutBlock_1": []})), api_key="k")
    sync_official_daily(db, date(2026, 11, 25), empty)
    assert db.query(FetchLog).filter_by(status="not_published").count() >= 1


def test_etf_not_linked_to_disclosures(calendar_2026):
    from app.services.briefings import _disclosure_facts
    db = calendar_2026
    add_instrument(db, "091160", "KODEX 테스트", etf=True)
    import app.providers.dart as dart_mod
    orig = dart_mod.DartProvider.__init__

    def fake_init(self, http=None, api_key=None):
        orig(self, http=http, api_key="k")
    dart_mod.DartProvider.__init__ = fake_init
    try:
        facts, status = _disclosure_facts(db, ["091160"], D, D)
    finally:
        dart_mod.DartProvider.__init__ = orig
    assert facts == [] and status[0]["status"] == "skipped"
