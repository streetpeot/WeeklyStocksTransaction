import pandas as pd

from modules import reporter


def _processed(with_etf=True):
    p = {"etf_market_agg": {"외국인": 5.0}}
    p["etf_flows"] = pd.DataFrame({
        "티커": ["229200"], "종목명": ["KODEX 코스닥150"],
        "1주외국인매매": [543.0], "1주기관매매": [-1171.0],
    }) if with_etf else pd.DataFrame()
    return p


def test_append_etf_section_adds_when_present():
    out = reporter._append_etf_section("본문", _processed(with_etf=True))
    assert "본문" in out
    assert "## ETF 수급" in out
    assert "KODEX 코스닥150" in out


def test_append_etf_section_noop_when_empty():
    out = reporter._append_etf_section("본문", _processed(with_etf=False))
    assert out == "본문"
