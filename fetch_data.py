"""
매크로 지표 데이터 수집 스크립트
GitHub Actions에서 매일 실행 -> data.json 업데이트
"""

import yfinance as yf
import requests
import json
import os
import re
import time
from datetime import datetime
from io import BytesIO
import pytz

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
KST = pytz.timezone("Asia/Seoul")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
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


def fetch_tenyear():
    d = get_yf("^TNX")
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
    return get_yf("^MOVE")


# ──────────────────────────────────────────────
# 6. CNN Fear & Greed
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
        rating = fg["rating"]

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
# 7. HY Spread (FRED)
# ──────────────────────────────────────────────
def fetch_hy_spread():
    if not FRED_API_KEY:
        return {"value": None, "change_abs": 0, "change_pct": 0, "history": [], "ok": False, "error": "FRED_API_KEY missing"}
    try:
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id=BAMLH0A0HYM2&api_key={FRED_API_KEY}"
            f"&sort_order=desc&limit=40&file_type=json"
        )
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        obs = [o for o in r.json()["observations"] if o["value"] != "."]

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
# FRED helper
# ──────────────────────────────────────────────
def _fred_series(series_id, limit=30):
    url = (
        f"https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_API_KEY}"
        f"&sort_order=desc&limit={limit}&file_type=json"
    )
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        return None
    obs = [o for o in r.json().get("observations", []) if o["value"] != "."]
    return obs if obs else None


# ──────────────────────────────────────────────
# 8. Bull-Bear Spread (AAII)
# ──────────────────────────────────────────────
def _try_aaii_html():
    """AAII sentiment page scrape with up to 3 retries."""
    for attempt in range(3):
        try:
            session = requests.Session()
            session.headers.update(HEADERS)

            # Get cookies first
            session.get("https://www.aaii.com/", timeout=15)
            time.sleep(2 + attempt * 2)

            r = session.get("https://www.aaii.com/sentimentsurvey/sent_results", timeout=15)
            r.raise_for_status()
            if "Pardon Our Interruption" in r.text or len(r.text) < 10000:
                continue

            rows = re.findall(
                r'class="tableTxt">(\d{1,2}\.\d)\s*%\s*</td>\s*'
                r'<td[^>]*class="tableTxt">(\d{1,2}\.\d)\s*%</td>\s*'
                r'<td[^>]*class="tableTxt">(\d{1,2}\.\d)\s*%',
                r.text
            )
            if not rows or len(rows) < 2:
                continue

            bull    = round(float(rows[0][0]), 1)
            bear    = round(float(rows[0][1]), 1)
            neutral = round(float(rows[0][2]), 1)
            spread  = round(bull - bear, 1)
            spread1 = round(float(rows[1][0]) - float(rows[1][1]), 1)
            history = [round(float(b) - float(br), 1) for b, br, _ in rows[:20]][::-1]

            return {
                "value": spread,
                "bull": bull, "bear": bear, "neutral": neutral,
                "change_abs": round(spread - spread1, 1),
                "change_pct": 0,
                "history": history,
                "source_note": "AAII Sentiment Survey",
                "ok": True,
            }
        except Exception:
            time.sleep(3)
    return None


def _try_fred_aaii():
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
            except Exception:
                continue
    return None


def fetch_bull_bear():
    result = _try_aaii_html()
    if result and result.get("ok"):
        return result
    if FRED_API_KEY:
        result = _try_fred_aaii()
        if result:
            return result
    return None


# ──────────────────────────────────────────────
# 9. Margin Debt YoY (FRED)
# ──────────────────────────────────────────────
def fetch_margin_debt():
    if not FRED_API_KEY:
        return {"value": None, "yoy": None, "change_abs": 0, "change_pct": 0,
                "history": [], "ok": False, "error": "FRED_API_KEY missing"}
    try:
        obs = _fred_series("BOGZ1FL663067003Q", 12)
        if not obs:
            raise Exception("BOGZ1FL663067003Q series not found")

        latest   = float(obs[0]["value"])
        year_ago = float(obs[4]["value"]) if len(obs) >= 5 else latest
        yoy = round((latest - year_ago) / year_ago * 100, 1) if year_ago else 0
        history = [round(float(o["value"]) / 1e6, 2) for o in reversed(obs)]

        return {
            "value": round(latest / 1e6, 2),
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
# 10. Put/Call Ratio (SPY options)
# ──────────────────────────────────────────────
def fetch_put_call():
    try:
        spy = yf.Ticker("SPY")
        exps = spy.options[:4]
        total_put = 0
        total_call = 0
        for exp in exps:
            try:
                chain = spy.option_chain(exp)
                total_put  += chain.puts["volume"].fillna(0).sum()
                total_call += chain.calls["volume"].fillna(0).sum()
            except Exception:
                pass

        if total_call == 0:
            raise Exception("option volume = 0")

        current = round(total_put / total_call, 3)
        return {
            "value": current,
            "change_abs": 0,
            "change_pct": 0,
            "history": [],
            "note": f"SPY {len(exps)} expiry (put {int(total_put):,} / call {int(total_call):,})",
            "ok": True,
        }
    except Exception as e:
        return {"value": None, "change_abs": 0, "change_pct": 0, "history": [], "ok": False, "error": str(e)}


# ──────────────────────────────────────────────
# Fallback: reuse last known value from data.json
# ──────────────────────────────────────────────
def _load_previous(key):
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            prev = json.load(f)
        ind = prev.get("indicators", {}).get(key, {})
        if ind.get("ok") and ind.get("value") is not None:
            ind["stale"] = True
            return ind
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    now = datetime.now(KST)
    print(f"[{now.strftime('%Y-%m-%d %H:%M KST')}] fetch start")

    def resolve(key, fn):
        result = fn()
        if result and result.get("ok"):
            return result
        fallback = _load_previous(key)
        if fallback:
            print(f"  [STALE] {key}: using previous value")
            return fallback
        return result or {"value": None, "change_abs": 0, "change_pct": 0, "history": [], "ok": False, "error": "fetch failed"}

    indicators = {
        "tenyear":    {"name": "미 10년물 금리",      "unit": "%",  "source": "yfinance", **fetch_tenyear()},
        "dxy":        {"name": "달러인덱스 (DXY)", "unit": "",   "source": "yfinance", **fetch_dxy()},
        "wti":        {"name": "유가 (WTI)",      "unit": "USD","source": "yfinance", **fetch_wti()},
        "vix":        {"name": "VIX 지수",                "unit": "",   "source": "yfinance", **fetch_vix()},
        "move":       {"name": "MOVE 지수",         "unit": "",   "source": "yfinance", **fetch_move()},
        "feargreed":  {"name": "공포탐욕지수 (CNN)", "unit": "",   "source": "CNN",      **fetch_fear_greed()},
        "hyspread":   {"name": "하이일드 스프레드",          "unit": "%p", "source": "FRED",     **fetch_hy_spread()},
        "bullbear":   {"name": "Bull-Bear Spread",   "unit": "%p", "source": "AAII",     **resolve("bullbear", fetch_bull_bear)},
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

    print("Done:")
    for key, ind in indicators.items():
        status = "OK" if ind.get("ok") else "NG"
        stale  = " [stale]" if ind.get("stale") else ""
        print(f"  {status} {key:12s} {str(ind.get('value')):>10}{stale}")


if __name__ == "__main__":
    main()
