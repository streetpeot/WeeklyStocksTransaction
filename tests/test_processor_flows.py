import pandas as pd
import pytest

from modules import processor


def _raw(krx_flows, flow_source):
    kospi = pd.DataFrame({
        "티커": ["005930", "000660"],
        "종목명": ["삼성전자", "SK하이닉스"],
        "시가총액(억)": [4000000.0, 1500000.0],
        "시장": ["KOSPI", "KOSPI"],
        # Naver 상세에서 온 기존 수급 (상위 200 표본)
        "1주기관매매": [999.0, None],
        "1주외국인매매": [888.0, None],
    })
    return {
        "kospi": kospi, "kosdaq": pd.DataFrame(),
        "investor_ranks": {}, "market_info": {},
        "base_date": "20260717", "krx_flows": krx_flows, "flow_source": flow_source,
    }


def test_krx_flows_replace_naver_values():
    krx = pd.DataFrame({
        "티커": ["005930", "000660"], "시장": ["KOSPI", "KOSPI"],
        "1주기관매매": [1.5, -3.0], "1주외국인매매": [-2.0, 4.0],
    })
    out = processor.process(_raw(krx, "krx"))
    df = out["kospi"]
    assert list(df.columns).count("1주기관매매") == 1  # 중복 컬럼 없음
    s = df.set_index("티커")
    assert s.loc["005930", "1주기관매매"] == pytest.approx(1.5)   # 999 아님 — KRX로 대체
    assert s.loc["000660", "1주외국인매매"] == pytest.approx(4.0)  # 표본 밖 종목도 채워짐
    assert out["flow_source"] == "krx"
    # 파생 비중도 KRX 값 기준
    assert s.loc["005930", "시가대비_기관매매비중_1주"] == pytest.approx(1.5 / 4000000 * 100)


def test_naver_fallback_keeps_existing_values():
    out = processor.process(_raw(pd.DataFrame(), "naver"))
    s = out["kospi"].set_index("티커")
    assert s.loc["005930", "1주기관매매"] == pytest.approx(999.0)
    assert out["flow_source"] == "naver"
