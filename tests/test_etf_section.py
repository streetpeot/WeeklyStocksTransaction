import pandas as pd

from modules import etf_section


def _flows():
    return pd.DataFrame({
        "티커": ["229200", "069500", "379800", "252670", "233740"],
        "종목명": ["KODEX 코스닥150", "KODEX 200", "KODEX 미국S&P500", "KODEX 200선물인버스2X", "KODEX 코스닥150레버리지"],
        "1주외국인매매": [543.0, 120.0, -80.0, -250.0, 30.0],
        "1주기관매매": [-1171.0, 300.0, 50.0, 40.0, -90.0],
    })


def test_empty_returns_blank():
    assert etf_section.build_etf_section(pd.DataFrame(), None) == ""
    assert etf_section.build_etf_section(None, None) == ""


def test_section_has_heading_agg_and_directions():
    md = etf_section.build_etf_section(_flows(), {"외국인": 5.0, "기관": -19568.0, "개인": 19048.0})
    assert md.startswith("## ETF 수급")
    assert "ETF 시장 전체" in md and "외국인 +5억" in md
    for title in ["외국인 순매수 상위", "외국인 순매도 상위", "기관 순매수 상위", "기관 순매도 상위"]:
        assert title in md
    # 외국인 순매수 1위 = KODEX 코스닥150(+543)
    fore_buy = md.split("외국인 순매수 상위")[1].split("외국인 순매도 상위")[0]
    assert "KODEX 코스닥150" in fore_buy
    # 외국인 순매도 1위 = KODEX 200선물인버스2X(-250)
    fore_sell = md.split("외국인 순매도 상위")[1].split("기관 순매수 상위")[0]
    assert "KODEX 200선물인버스2X" in fore_sell
    # 기관 순매수 1위 = KODEX 200(+300)
    inst_buy = md.split("기관 순매수 상위")[1].split("기관 순매도 상위")[0]
    assert "KODEX 200" in inst_buy
    # 기관 순매도 1위 = KODEX 코스닥150(-1171)
    inst_sell = md.split("기관 순매도 상위")[1].split("> 최상위")[0]
    assert "KODEX 코스닥150" in inst_sell


def test_agg_omitted_when_none():
    md = etf_section.build_etf_section(_flows(), None)
    assert "ETF 시장 전체" not in md
    assert "외국인 순매수 상위" in md      # 표는 유지


def test_agg_skips_none_or_nan_values_without_crash():
    """agg 값이 None/NaN인 키는 크래시 없이 집계 줄에서 제외(결정적 안전 렌더 계약)."""
    md = etf_section.build_etf_section(_flows(), {"외국인": 5.0, "기관": None, "개인": float("nan")})
    agg_line = next(l for l in md.splitlines() if l.startswith("**ETF 시장 전체**"))
    assert "외국인 +5억" in agg_line
    assert "기관" not in agg_line
    assert "개인" not in agg_line


def test_missing_name_falls_back_to_ticker():
    """종목명이 None인 행은 셀에 'None'을 렌더하지 않고 티커로 폴백."""
    flows = _flows()
    flows.loc[flows["티커"] == "229200", "종목명"] = None
    md = etf_section.build_etf_section(flows, None)
    assert "229200" in md
    assert "None" not in md
