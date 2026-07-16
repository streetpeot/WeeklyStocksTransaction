from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from modules import watchlist

WL_MD = """---
title: 워치리스트
type: topic
---
# 워치리스트

| 티커 | 종목명 | 구분 | 메모 |
|---|---|---|---|
| 005930 | 삼성전자 | 보유 | [[삼성전자]] |
| 380540 | 옵티코어 | 관심 | 로보틱스 테제 |
| 잘못된행 | - | - | 티커 아님 |
"""


def _processed():
    return {
        "kospi": pd.DataFrame({
            "티커": ["005930"], "종목명": ["삼성전자"], "시가총액": [4000000.0],
            "1주등락률": [1.2], "1주기관매매": [1.5], "1주외국인매매": [-2.0],
            "시장": ["KOSPI"],
        }),
        "kosdaq": pd.DataFrame({
            "티커": ["380540"], "종목명": ["옵티코어"], "시가총액": [1500.0],
            "1주등락률": [-3.4], "1주기관매매": [0.3], "1주외국인매매": [0.8],
            "시장": ["KOSDAQ"],
        }),
        "base_date": "20260717",
    }


def _db():
    db = MagicMock()
    db.get_stock_flow_history.return_value = pd.DataFrame({
        "week_date": ["20260710", "20260717"],
        "inst_net_1w": [2.0, 1.5], "foreign_net_1w": [-1.0, -2.0],
    })
    return db


def test_load_watchlist_parses_ticker_rows(tmp_path):
    p = tmp_path / "워치리스트.md"
    p.write_text(WL_MD, encoding="utf-8")
    wl = watchlist.load_watchlist(p)
    assert list(wl["티커"]) == ["005930", "380540"]   # 6자리 숫자만
    assert list(wl["구분"]) == ["보유", "관심"]


def test_load_watchlist_missing_file(tmp_path):
    assert watchlist.load_watchlist(tmp_path / "없음.md").empty


def test_build_section_contains_stocks_and_trend():
    wl = pd.DataFrame({"티커": ["005930", "380540"],
                       "종목명": ["삼성전자", "옵티코어"], "구분": ["보유", "관심"]})
    md = watchlist.build_watchlist_section(wl, _processed(), _db())
    assert md.startswith("## 워치리스트 수급")
    assert "삼성전자" in md and "옵티코어" in md
    assert "+1.5" in md    # 이번 주 기관
    assert "→" in md       # 추이 표기


def test_generate_personal_report(tmp_path):
    wl_path = tmp_path / "워치리스트.md"
    wl_path.write_text(WL_MD, encoding="utf-8")
    report = tmp_path / "주가자금동향_20260717.md"
    report.write_text("---\ntitle: x\n---\n본문", encoding="utf-8")
    config = {"publish": {"watchlist_path": str(wl_path)},
              "output": {"report_prefix": "주가자금동향"}}

    out = watchlist.generate_personal_report(str(report), _processed(), _db(), config)

    assert out is not None and out.endswith("주가자금동향_개인_20260717.md")
    text = Path(out).read_text(encoding="utf-8")
    assert text.startswith("---\ntitle: x")           # 공유용 본문 보존
    assert "## 워치리스트 수급" in text
    shared = report.read_text(encoding="utf-8")
    assert "워치리스트" not in shared                  # 공유용 무오염


def test_generate_personal_report_no_watchlist(tmp_path):
    report = tmp_path / "주가자금동향_20260717.md"
    report.write_text("본문", encoding="utf-8")
    config = {"publish": {"watchlist_path": str(tmp_path / "없음.md")},
              "output": {"report_prefix": "주가자금동향"}}
    assert watchlist.generate_personal_report(str(report), _processed(), _db(), config) is None
