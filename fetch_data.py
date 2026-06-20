"""
매크로 지표 데이터 수집 스크립트
GitHub Actions에서 매일 실행 → data.json 업데이트

지표:
  1. 미 10년물 금리     (yfinance: ^TNX)
  2. 달러인덱스 DXY     (yfinance: DX-Y.NYB)
  3. 유가 WTI          (yfinance: CL=F)
  4. VIX 지수          (yfinance: ^VIX)
  5. MOVE 지수         (yfinance: ^MOVE)
  6. 공포탐욕지수       (CNN unofficial API)
  7. 하이일드 스프레드   (FRED API: BAMLH0A0HYM2)
  8. Bull-Bear Spread  (FRED API: AAIIBULL, AAIIBEAR — 주간)
  9. Margin Debt YoY   (FRED API: BOGZ1FL663067003Q — 분기)
 10. Put/Call Ratio    (yfinance: SPY 옵션체인 근거리 3만기 합산)
"""

import yfinance as yf
import requests
import json
import os
import re
from datetime import datetime, date, timedelta
from io import BytesIO

import pytz

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
KST = pytz.timezone("Asia/Seoul")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ──────────────────────────────────────────────
# yfinance 공통
# ──────────────────────────────────────────────
def get_yf(ticker):
    try:
        t = yf.Ticker(ticker)
        info = t.info
        hist = t.history(period="60d")

        current = info.get("regularMarketPrice")
        prev = info.get("regularMarketPreviousClose")

        if not current and not hist.empty:
            current = float(hist["Close"].iloc[-1])
        if not prev and len(hist) > 1:
            prev = float(hist["Close"].iloc[-2])

        change_pct = round((current - prev) / prev * 100, 2) if current and prev else 0
        change_abs = round(current - prev, 4) if current and prev else 0
        history = [round(float(v), 3) for v in hist["Close"].tolist()[-20:]]

        return {
            "value": round(current, 3) if current else None,
            "prev": round(prev, 3) if prev else None,
            "change_pct": change_pct,
            "change_abs": change_abs,
            "history": history,
            "ok": True,
        }
    except Exception as e:
        return {"value": None, "change_pct": 0, "change_abs": 0, "history": [], "ok": False, "error": str(e)}


# ──────────────────────────────────────────────
# 1~5: yfinance 지표
# ──────────────────────────────────────────────
def fetch_tenyear():
    d = get_yf("^TNX")
    # TNX는 10 기준이므로 그대로 사용 (예: 43.8 → 4.38%)
    # yfinance가 실제 % 값으로 반환하는 경우도 있음
    if d["value"] and d["value"] > 20:
        d["value"] = round(d["value"] / 10, 3)
        d["prev"] = round(d["prev"] / 10, 3) if d["prev"] else None
        d["change_abs"] = round(d["change_abs"] / 10, 4)
        d["history"] = [round(v / 10, 3) for v in d["history"]]
    return d


def fetch_dxy():
    return get_yf("DX-Y.NYB")


def fetch_wti():
    return get_yf("CL=F")


def fetch_vix():
    return get_yf("^VIX")


def fetch_move():
    d = get_yf("^MOVE")
    if not d["ok"] or d["value"] is None:
        # 대안: ICE BofAML MOVE Index via FRED (MOODCMDM)
        # 없으면 None 반환
        pass
    return d


# ──────────────────────────────────────────────
# 6. 공포탐욕지수 (CNN)
# ──────────────────────────────────────────────
def fetch_fear_greed():
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers=HEADERS, timeout=15
        )
        r.raise_for_status()
        data = r.json()

        fg = data["fear_and_greed"]
        score = round(float(fg["score"]), 1)
        rating = fg["rating"]  # e.g. "Greed", "Fear", "Neutral"

        hist_raw = data.get("fear_and_greed_historical", {}).get("data", [])
        history = [round(float(d["y"]), 1) for d in hist_raw[-20:]]
        prev = history[-2] if len(history) >= 2 else score

        return {
            "value": score,
            "rating": rating,
            "change_abs": round(score - prev, 1),
            "change_pct": 0,
            "history": history,
            "ok": True,
        }
    except Exception as e:
        return {"value": None, "rating": "N/A", "change_abs": 0, "change_pct": 0, "history": [], "ok": False, "error": str(e)}


# ──────────────────────────────────────────────
# 7. 하이일드 스프레드 (FRED)
# ──────────────────────────────────────────────
def fetch_hy_spread():
    if not FRED_API_KEY:
        return {"value": None, "change_abs": 0, "change_pct": 0, "history": [], "ok": False, "error": "FRED_API_KEY 없음"}
    try:
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id=BAMLH0A0HYM2&api_key={FRED_API_KEY}"
            f"&sort_order=desc&limit=40&file_type=json"
        )
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        obs = [o for o in data["observations"] if o["value"] != "."]

        current = round(float(obs[0]["value"]), 2)
        prev = round(float(obs[1]["value"]), 2)
        history = [round(float(o["value"]), 2) for o in reversed(obs[:20])]

        return {
            "value": current,
            "date": obs[0]["date"],
            "change_abs": round(current - prev, 2),
            "change_pct": round((current - prev) / prev * 100, 2) if prev else 0,
            "history": history,
            "ok": True,
        }
    except Exception as e:
        return {"value": None, "change_abs": 0, "change_pct": 0, "history": [], "ok": False, "error": str(e)}


# ──────────────────────────────────────────────
# FRED 공통 헬퍼
# ──────────────────────────────────────────────
def _fred_series(series_id, limit=30):
    """FRED 시리즈 최신 관측값 리스트 반환. 에러 시 None 반환."""
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_API_KEY}"
        f"&sort_order=desc&limit={limit}&file_type=json"
    )
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        return None   # 시리즈 없음 or 오류
    obs = [o for o in r.json().get("observations", []) if o["value"] != "."]
    return obs if obs else None


# ──────────────────────────────────────────────
# 8. Bull-Bear Spread
#    1순위: AAII via NAAIM 사이트 (엑셀 다운로드)
#    2순위: FRED USAAIIBULL / USAAIIBEAR
#    3순위: FRED UMCSENT (소비자심리 대체 지표)
# ──────────────────────────────────────────────
def _try_naaim():
    """NAAIM Exposure Index (운용사 주식 노출도, 주간) — Bull-Bear 대체
    URL이 날짜마다 바뀌므로 페이지를 먼저 긁어 현재 링크를 찾는다.
    """
    try:
        import io, zipfile, xml.etree.ElementTree as ET

        # 1. NAAIM 페이지에서 현재 xlsx URL 찾기
        page = requests.get(
            "https://www.naaim.org/programs/naaim-exposure-index/",
            headers=HEADERS, timeout=15
        )
        page.raise_for_status()
        links = re.findall(r'href=["\']([^"\']*\.xlsx[^"\']*)["\']', page.text, re.IGNORECASE)
        if not links:
            return None
        xlsx_url = links[0]

        # 2. 엑셀 다운로드
        r = requests.get(xlsx_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        if r.content[:4] != b'PK\x03\x04':  # xlsx 시그니처 확인
            return None

        # 3. xlsx = zip 파일, 숫자 컬럼 파싱
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        sheet_xml = zf.read("xl/worksheets/sheet1.xml")
        ns = {"ns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        sheet_root = ET.fromstring(sheet_xml)
        rows = sheet_root.findall(".//ns:row", ns)

        values = []
        for row in rows[1:]:   # 헤더 스킵
            cells = row.findall("ns:c", ns)
            if len(cells) >= 2:
                try:
                    v_el = cells[1].find("ns:v", ns)
                    t_attr = cells[1].get("t", "")
                    if v_el is not None and t_attr != "s":  # s=sharedString(문자열) 스킵
                        values.append(round(float(v_el.text), 1))
                except:
                    pass

        if len(values) < 2:
            return None

        current = values[-1]
        prev    = values[-2]
        history = values[-20:]

        return {
            "value": current,
            "bull": None, "bear": None, "neutral": None,
            "change_abs": round(current - prev, 1),
            "change_pct": 0,
            "history": history,
            "source_note": "NAAIM Exposure Index (주간)",
            "ok": True,
        }
    except Exception as e:
        return None


def _try_fred_aaii():
    """FRED에서 가능한 AAII 시리즈 ID를 순서대로 시도"""
    # FRED에 AAII 관련으로 알려진 시리즈 ID 후보
    candidates = [
        ("USAAIIBULL", "USAAIIBEAR"),
        ("AAIIBULL",   "AAIIBEAR"),
    ]
    for bull_id, bear_id in candidates:
        bull_obs = _fred_series(bull_id, 20)
        bear_obs = _fred_series(bear_id, 20)
        if bull_obs and bear_obs:
            try:
                bull  = round(float(bull_obs[0]["value"]), 1)
                bear  = round(float(bear_obs[0]["value"]), 1)
                bull1 = round(float(bull_obs[1]["value"]), 1)
                bear1 = round(float(bear_obs[1]["value"]), 1)
                spread      = round(bull - bear, 1)
                spread_prev = round(bull1 - bear1, 1)

                bull_map = {o["date"]: float(o["value"]) for o in bull_obs}
                bear_map = {o["date"]: float(o["value"]) for o in bear_obs}
                dates = sorted(set(bull_map) & set(bear_map))
                history = [round(bull_map[d] - bear_map[d], 1) for d in dates]

                return {
                    "value": spread,
                    "bull": bull, "bear": bear,
                    "neutral": round(100 - bull - bear, 1),
                    "change_abs": round(spread - spread_prev, 1),
                    "change_pct": 0,
                    "history": history,
                    "source_note": f"AAII via FRED ({bull_id})",
                    "ok": True,
                }
            except:
                continue
    return None


def fetch_bull_bear():
    # 1순위: NAAIM (API 키 불필요)
    result = _try_naaim()
    if result:
        return result

    # 2순위: FRED AAII (키 필요)
    if FRED_API_KEY:
        result = _try_fred_aaii()
        if result:
            return result

    err = "NAAIM 수집 실패" if not FRED_API_KEY else "NAAIM·AAII 모두 수집 실패"
    return {"value": None, "bull": None, "bear": None, "neutral": None,
            "change_abs": 0, "change_pct": 0, "history": [], "ok": False,
            "error": err}


# ──────────────────────────────────────────────
# 9. Margin Debt YoY (FRED: BOGZ1FL663067003Q — 분기)
# ──────────────────────────────────────────────
def fetch_margin_debt():
    if not FRED_API_KEY:
        return {"value": None, "yoy": None, "change_abs": 0, "change_pct": 0,
                "history": [], "ok": False, "error": "FRED_API_KEY 없음"}
    try:
        # BOGZ1FL663067003Q: Broker-Dealer Net Debit Balances (분기, 백만 달러)
        obs = _fred_series("BOGZ1FL663067003Q", 12)  # 12분기 = 3년
        if not obs:
            raise Exception("BOGZ1FL663067003Q 시리즈 없음")

        latest   = float(obs[0]["value"])   # 백만 달러
        year_ago = float(obs[4]["value"]) if len(obs) >= 5 else latest  # 4분기 전 = 1년 전
        yoy = round((latest - year_ago) / year_ago * 100, 1) if year_ago else 0

        # 히스토리는 조 달러 단위
        history = [round(float(o["value"]) / 1e6, 2) for o in reversed(obs)]

        return {
            "value": round(latest / 1e6, 2),   # 조 달러
            "yoy": yoy,
            "change_abs": yoy,
            "change_pct": yoy,
            "date": obs[0]["date"],
            "history": history,
            "ok": True,
        }
    except Exception as e:
        return {"value": None, "yoy": None, "change_abs": 0, "change_pct": 0,
                "history": [], "ok": False, "error": str(e)}


# ──────────────────────────────────────────────
# 10. Put/Call Ratio (yfinance: SPY 옵션체인)
#     CBOE 정적 파일은 JS 렌더로 차단됨 → SPY 근거리 3만기 합산으로 대체
# ──────────────────────────────────────────────
def fetch_put_call():
    try:
        spy = yf.Ticker("SPY")
        exps = spy.options[:4]   # 근거리 4개 만기 사용
        total_put = 0
        total_call = 0
        for exp in exps:
            try:
                chain = spy.option_chain(exp)
                total_put  += chain.puts["volume"].fillna(0).sum()
                total_call += chain.calls["volume"].fillna(0).sum()
            except:
                pass

        if total_call == 0:
            raise Exception("옵션 거래량 0")

        current = round(total_put / total_call, 3)

        # 히스토리: yfinance로 일별 히스토리 없으므로 단일값만 반환
        return {
            "value": current,
            "change_abs": 0,       # 전일 대비는 다음 실행 시 data.json 비교로 추후 구현 가능
            "change_pct": 0,
            "history": [],
            "note": f"SPY 옵션 {len(exps)}만기 합산 (put {int(total_put):,} / call {int(total_call):,})",
            "ok": True,
        }
    except Exception as e:
        return {"value": None, "change_abs": 0, "change_pct": 0, "history": [], "ok": False, "error": str(e)}


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
def main():
    now = datetime.now(KST)
    print(f"[{now.strftime('%Y-%m-%d %H:%M KST')}] 데이터 수집 시작")

    indicators = {
        "tenyear":    {"name": "미 10년물 금리",    "unit": "%",  "source": "yfinance", **fetch_tenyear()},
        "dxy":        {"name": "달러인덱스 (DXY)",   "unit": "",   "source": "yfinance", **fetch_dxy()},
        "wti":        {"name": "유가 (WTI)",         "unit": "USD","source": "yfinance", **fetch_wti()},
        "vix":        {"name": "VIX 지수",           "unit": "",   "source": "yfinance", **fetch_vix()},
        "move":       {"name": "MOVE 지수",          "unit": "",   "source": "yfinance", **fetch_move()},
        "feargreed":  {"name": "공포탐욕지수 (CNN)",  "unit": "",   "source": "CNN",      **fetch_fear_greed()},
        "hyspread":   {"name": "하이일드 스프레드",   "unit": "%p", "source": "FRED",     **fetch_hy_spread()},
        "bullbear":   {"name": "Bull-Bear Spread",   "unit": "%p", "source": "AAII",     **fetch_bull_bear()},
        "margindebt": {"name": "Margin Debt YoY",    "unit": "%",  "source": "FINRA",    **fetch_margin_debt()},
        "putcall":    {"name": "Put/Call Ratio",      "unit": "",   "source": "CBOE",     **fetch_put_call()},
    }

    result = {
        "updated_at": now.strftime("%Y-%m-%d %H:%M KST"),
        "updated_at_unix": int(now.timestamp()),
        "indicators": indicators,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("완료! 결과 요약:")
    for key, ind in indicators.items():
        status = "✓" if ind.get("ok") else "✗"
        val = ind.get("value")
        chg = ind.get("change_abs", 0) or 0
        print(f"  {status} {ind['name']:22s} {str(val):>10}  ({chg:+.2f})")


if __name__ == "__main__":
    main()
