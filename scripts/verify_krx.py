"""KRX 일괄 API 검증 게이트 — 스펙 §4.2. 수동 실행:
PYTHONPATH=".venv/lib/python3.11/site-packages:." .venv/bin/python scripts/verify_krx.py
사전: 키체인 krx-data 등록 (README 사전 조건 참조)
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")
from modules import krx_auth  # noqa: E402  (Task 2에서 구현 — 그 전엔 환경변수 직접 설정으로 실행 가능)


def last_full_week() -> tuple[str, str]:
    today = date.today()
    friday = today - timedelta(days=(today.weekday() - 4) % 7)
    if friday == today:
        friday -= timedelta(days=7)
    monday = friday - timedelta(days=4)
    return monday.strftime("%Y%m%d"), friday.strftime("%Y%m%d")


def main() -> int:
    import os
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        try:
            krx_auth.inject_credentials()
        except Exception as e:
            print(f"자격증명 주입 실패: {e}")
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        print("BLOCKED: 키체인 krx-data 미등록 — 사전 조건 수행 필요")
        return 2

    from pykrx import stock
    monday, friday = last_full_week()
    print(f"검증 주간: {monday}~{friday}")
    failures = []

    # ① 전종목 반환 여부 (순매수'상위'종목 화면이 전체를 주는지)
    for market, floor in [("KOSPI", 1500), ("KOSDAQ", 1400)]:
        df = stock.get_market_net_purchases_of_equities_by_ticker(monday, friday, market, "외국인")
        print(f"① {market} 외국인 rows={len(df)} (기준 ≥{floor})")
        if len(df) < floor:
            failures.append(f"{market} 전종목 미반환({len(df)}행)")

    # ② 소형주 포함 + 값 형태 (볼트 엔티티 중 시총 200위 밖)
    df_ksq = stock.get_market_net_purchases_of_equities_by_ticker(monday, friday, "KOSDAQ", "기관합계")
    for t, name in [("380540", "옵티코어"), ("200710", "에이디테크놀로지")]:
        if t in df_ksq.index:
            print(f"② {name}({t}) 기관 순매수: {df_ksq.loc[t, '순매수거래대금'] / 1e8:.1f}억")
        else:
            failures.append(f"{name}({t}) 미포함")

    # ③ 당일 데이터 반영 시각 — 오늘(거래일 저녁 실행 시) 데이터 존재 여부
    today_s = date.today().strftime("%Y%m%d")
    df_today = stock.get_market_net_purchases_of_equities_by_ticker(today_s, today_s, "KOSPI", "외국인")
    print(f"③ 당일({today_s}) 데이터: {len(df_today)}행 "
          f"{'— 당일 저녁 반영 확인' if len(df_today) else '— 미반영(장중/휴장이면 정상, 금 20시 재확인 필요)'}")

    # ④ 값 정합성 참고 출력 — 삼성전자 주간치 (Naver 보고서 값과 사람이 대조)
    if "005930" in stock.get_market_net_purchases_of_equities_by_ticker(monday, friday, "KOSPI", "기관합계").index:
        df_k = stock.get_market_net_purchases_of_equities_by_ticker(monday, friday, "KOSPI", "기관합계")
        df_f = stock.get_market_net_purchases_of_equities_by_ticker(monday, friday, "KOSPI", "외국인")
        print(f"④ 삼성전자 기관 {df_k.loc['005930', '순매수거래대금'] / 1e8:+.0f}억 / "
              f"외국인 {df_f.loc['005930', '순매수거래대금'] / 1e8:+.0f}억 "
              f"→ data/주가자금동향_{friday}.md 값과 부호·자릿수 대조할 것")

    print("\n결과:", "FAIL — " + "; ".join(failures) if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
