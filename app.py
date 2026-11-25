import io
import json
import time
import zipfile
from datetime import date, timedelta
from typing import Any

import pandas as pd
import requests
import streamlit as st

APIFY_ACTOR = "mansalabs~egx-stock-data"
APIFY_BASE = "https://api.apify.com/v2"
APP_VERSION = "2.0.0"

st.set_page_config(page_title="EGX Daily Candle Downloader", page_icon="📈", layout="wide")
st.title("📈 EGX Daily Candle Downloader")
st.caption(f"نسخة مستقلة لتنزيل شموع EGX اليومية OHLCV — الإصدار {APP_VERSION}")

DEFAULT_SYMBOLS = """COMI
MFPC
PHDC
ORAS
HDBK
EFIH
AMES
INEG
BTFH
BIOC
CLHO
MBSC
MTIE
EGTS
EGSA
MHOT
EGBE
IFAP
PRDC
MIPH
MPCI
MOIN
ISMQ
AXPH
PHTV
CPCI
NINH
SPIN
ENGC
CNFN
SVCE
KABO
OFH
GSSC
WCDF
MFSC
SAIB
ACGC
UEFM
KZPC
ADCI
INFI
ASCM
ZEOT
SMFR
ETRS
CIRA
EDFM
MILS
GBCO
SCTS
HRHO
TMGH
FWRY
SWDY
ETEL
AMOC
HELI
EAST
EFID
JUFO
ABUK
EMFD
CCAP
CICH
OCDI
ORHD
MASR
ADIB
SAUD
CIEB
FAIT
AFDI
CANA
EXPA
ARCC
AJWA
MICH
SUGR
POUL
DOMT
ISMA
UEGC
FERC
FAITA
MAAL
GGCC
NDRL
EFIC
RTVC
RUBX
PRMH
UNIP
ICLE
MEGM
EASB
APSW
MOED
KWIN
RMDA
OIH
SPHT
SDTI
GTWL
CFGH
NAHO
ACAMD
CEFM
ASPI
SCFM
CERA
DEIN
MBEG
SIPC
NHPS
ROTO
RAKT
EEII
CCRS
AREH
EPCO
GRCA
GIHD
ELWA
MMAT
NEDA
EPPK
GMCI
GPPL
ADPC
OBRI
RREI
RKAZ
SEIG
SNFC
WKOL
LUTS
AIFI
AMIA
DTPP
EALR
EBSC
EGREF
EHDR
ELNA
EOSB
FIRE
FNAR
ICID
IDRE
LCSW
MOSC
OLFI
SKPC
AMER
TALM
ALUM
ORWE
SPMD
ZMID
MENA
DAPH
RAYA
EGAL
MPRC
AFMC
NCCW
SCEM
ARAB
GDWA
ELEC
IRON
ATQA
EGCH
ALCN
MPCO
ELSH
MEPA
ODIN
EGAS
RACC
PRCL
BINV
MCQE
MOIL
NIPH
ISPH
DSCW
AALR
UNIT
PHAR
TRTO
CAED
CSAG
ICFC
ELKA
ATLC
COSG
AMPI
COPR
OCPH"""


def read_secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, "") or "").strip()
    except Exception:
        return ""


def parse_symbols(raw: str) -> list[str]:
    out = []
    for line in raw.replace(",", "\n").replace(";", "\n").splitlines():
        s = line.strip().upper().replace(".CA", "")
        if s and s not in out:
            out.append(s)
    return out


def apify_request(method: str, path: str, token: str, **kwargs):
    url = f"{APIFY_BASE}{path}"
    params = kwargs.pop("params", {})
    params["token"] = token
    response = requests.request(method, url, params=params, timeout=(20, 90), **kwargs)
    if not response.ok:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:500]
        raise RuntimeError(f"Apify HTTP {response.status_code}: {str(detail)[:700]}")
    return response


def run_history_actor(token: str, tickers: list[str], start_day: date, end_day: date, max_results: int, status_box):
    payload = {
        "dataset": "history",
        "tickers": tickers,
        "historyFrom": start_day.isoformat(),
        "historyTo": end_day.isoformat(),
        "maxResults": int(max_results),
    }
    started = apify_request("POST", f"/acts/{APIFY_ACTOR}/runs", token,
                            json=payload, params={"waitForFinish": 60}).json()
    run_data = started.get("data", {})
    run_id = run_data.get("id")
    if not run_id:
        raise RuntimeError(f"Apify did not return a run ID: {str(started)[:500]}")

    deadline = time.time() + 600
    status = run_data.get("status", "RUNNING")
    while status not in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
        if time.time() > deadline:
            raise TimeoutError(f"انتهت مهلة انتظار Apify. Run ID: {run_id} — راجع سجل التشغيل في حساب Apify.")
        status_box.info(f"حالة تشغيل مصدر البيانات: {status} — رقم التشغيل: {run_id}")
        time.sleep(4)
        poll = apify_request("GET", f"/actor-runs/{run_id}", token).json()
        run_data = poll.get("data", {})
        status = run_data.get("status", "UNKNOWN")

    if status != "SUCCEEDED":
        msg = run_data.get("statusMessage") or run_data.get("status_message") or status
        raise RuntimeError(f"فشل تشغيل Apify: {msg} (Run ID: {run_id})")

    dataset_id = run_data.get("defaultDatasetId")
    if not dataset_id:
        raise RuntimeError("التشغيل نجح لكن Apify لم يرجع defaultDatasetId")
    items = apify_request("GET", f"/datasets/{dataset_id}/items", token,
                          params={"format": "json", "clean": "true", "limit": 100000}).json()
    if not isinstance(items, list):
        raise RuntimeError(f"شكل بيانات Apify غير متوقع: {type(items).__name__}")
    return items, run_id, run_data


def normalize_item(item: dict[str, Any]):
    def pick(*keys):
        for key in keys:
            if item.get(key) is not None and item.get(key) != "":
                return item.get(key)
        return None

    ticker = pick("ticker", "symbol", "Ticker", "Symbol", "stock_symbol")
    day_value = pick("date", "trade_date", "trading_date", "Date", "timestamp")
    if day_value is None:
        return None
    try:
        day = pd.to_datetime(day_value, errors="coerce", utc=True)
        if pd.isna(day):
            day = pd.to_datetime(day_value, errors="coerce")
        if pd.isna(day):
            return None
        day = day.date()
    except Exception:
        return None

    row = {
        "symbol": str(ticker or "").upper().replace(".CA", ""),
        "date": day.isoformat(),
        "open": pick("open", "Open"),
        "high": pick("high", "High"),
        "low": pick("low", "Low"),
        "close": pick("close", "Close", "price"),
        "adj_close": pick("adj_close", "adjusted_close", "Adj Close"),
        "volume": pick("volume", "Volume"),
        "currency": pick("currency", "Currency") or "EGP",
        "source": "Apify / Mansa Labs EGX Stock Data",
    }
    for col in ("open", "high", "low", "close", "adj_close", "volume"):
        try:
            row[col] = float(row[col]) if row[col] is not None else None
        except (TypeError, ValueError):
            row[col] = None
    return row


def validate_candle(row: dict):
    missing = [k for k in ("open", "high", "low", "close", "volume") if row.get(k) is None]
    if missing:
        return False, "قيم ناقصة: " + ", ".join(missing)
    o, h, l, c, v = (row[k] for k in ("open", "high", "low", "close", "volume"))
    if min(o, h, l, c, v) < 0:
        return False, "قيمة سالبة في OHLCV"
    if h < max(o, l, c) or l > min(o, h, c):
        return False, "ترتيب أسعار OHLC غير منطقي"
    return True, "شمعة مكتملة وسليمة مبدئيًا"


with st.sidebar:
    st.header("إعدادات التحميل")
    saved_token = read_secret("APIFY_API_TOKEN") or read_secret("APIFY_TOKEN")
    token_input = st.text_input("Apify API Token", value="", type="password",
                                help="ضع APIFY_API_TOKEN في Streamlit Secrets أو أدخل التوكن هنا.")
    apify_token = token_input.strip() or saved_token
    if saved_token and not token_input.strip():
        st.success("تم العثور على APIFY_API_TOKEN في Secrets")
    st.divider()
    today = date.today()
    start_day = st.date_input("من تاريخ", value=today - timedelta(days=1), max_value=today)
    end_day = st.date_input("إلى تاريخ", value=today, min_value=start_day, max_value=today)
    max_results = st.number_input("أقصى عدد سجلات من المصدر", min_value=1, max_value=100000,
                                  value=5000, step=100)
    st.caption("التكلفة حسب صفحة المصدر: 15 دولار لكل 1,000 سجل + 0.005 دولار لكل تشغيل. راجع التسعير قبل التحميل الكبير.")

st.subheader("قائمة الأسهم")
col_a, col_b = st.columns([3, 1])
with col_a:
    symbols_raw = st.text_area("الرموز (تقدر تعدّل القائمة)", value=DEFAULT_SYMBOLS, height=220,
                               help="القائمة المحفوظة من مشروعك السابق. كل رمز في سطر.")
with col_b:
    symbols = parse_symbols(symbols_raw)
    st.metric("عدد الرموز", len(symbols))
    st.metric("الفترة", f"{(end_day-start_day).days + 1} يوم")
    st.caption("يتم إرسال الرموز في تشغيل واحد للمصدر.")

st.info("المصدر: Apify Actor — EGX Stock Data من Mansa Labs. يدعم historyFrom وhistoryTo وبيانات OHLCV اليومية. يلزم Apify API Token.")
st.warning("تنبيه: شمعة نفس اليوم قد لا تكون منشورة قبل انتهاء جلسة EGX وتحديث المصدر. البرنامج لا يستبدل شمعة اليوم بسعر لحظي ولا يختلق بيانات ناقصة.")

if st.button("🚀 تنزيل الشموع اليومية", type="primary", use_container_width=True):
    if not apify_token:
        st.error("أضف APIFY_API_TOKEN في Streamlit Secrets أو أدخل التوكن في الشريط الجانبي.")
        st.stop()
    if not symbols:
        st.error("قائمة الأسهم فارغة.")
        st.stop()
    if start_day > end_day:
        st.error("تاريخ البداية لازم يكون قبل تاريخ النهاية.")
        st.stop()

    status_box = st.empty()
    try:
        with st.spinner("جاري تشغيل مصدر بيانات EGX وانتظار الشموع..."):
            raw_items, run_id, run_info = run_history_actor(apify_token, symbols, start_day, end_day,
                                                             int(max_results), status_box)
        status_box.success(f"انتهى المصدر بنجاح — Run ID: {run_id} — السجلات المستلمة: {len(raw_items)}")

        normalized = []
        malformed = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            row = normalize_item(item)
            if row is None:
                malformed.append({"raw": json.dumps(item, ensure_ascii=False)[:500], "reason": "تعذر قراءة التاريخ"})
                continue
            if not row["symbol"]:
                malformed.append({"raw": json.dumps(item, ensure_ascii=False)[:500], "reason": "السجل لا يحتوي رمز سهم"})
                continue
            if start_day <= date.fromisoformat(row["date"]) <= end_day:
                normalized.append(row)

        by_symbol = {}
        for row in normalized:
            by_symbol.setdefault(row["symbol"], []).append(row)

        good_rows, bad_rows = [], []
        for ticker in symbols:
            rows = by_symbol.get(ticker, [])
            if not rows:
                bad_rows.append({"symbol": ticker + ".CA", "date": "", "status": "لا توجد بيانات",
                                 "message": "المصدر لم يرجع شموعًا لهذا السهم في الفترة المحددة"})
                continue
            for row in sorted(rows, key=lambda x: x["date"]):
                valid, message = validate_candle(row)
                if valid:
                    good_rows.append({**row, "symbol": ticker + ".CA"})
                else:
                    bad_rows.append({"symbol": ticker + ".CA", "date": row["date"],
                                     "status": "شمعة غير مكتملة/غير سليمة", "message": message})

        candles = pd.DataFrame(good_rows, columns=["symbol", "date", "open", "high", "low", "close",
                                                   "adj_close", "volume", "currency", "source"])
        report = pd.DataFrame(bad_rows, columns=["symbol", "date", "status", "message"])
        returned_count = len(normalized)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("شموع سليمة", len(candles))
        c2.metric("أسهم في القائمة", len(symbols))
        c3.metric("سجلات غير سليمة/ناقصة", len(bad_rows))
        c4.metric("السجلات المستلمة", returned_count)

        st.subheader("الشموع المكتملة")
        if candles.empty:
            st.error("المصدر لم يرجع أي شمعة مكتملة ضمن الفترة. لن يتم إنشاء ملف شموع فارغ على أنه نجاح.")
        else:
            st.dataframe(candles, use_container_width=True, hide_index=True)
            csv_candles = candles.to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ تحميل الشموع CSV", csv_candles,
                               file_name=f"EGX_daily_candles_{start_day}_{end_day}.csv",
                               mime="text/csv", use_container_width=True)

        st.subheader("تقرير الأسهم والبيانات الناقصة")
        if report.empty:
            st.success("كل الأسهم اللي رجع لها المصدر بياناتها مكتملة مبدئيًا.")
        else:
            st.dataframe(report, use_container_width=True, hide_index=True)

        report_csv = report.to_csv(index=False).encode("utf-8-sig")
        raw_csv = pd.DataFrame(raw_items).to_csv(index=False).encode("utf-8-sig")
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"EGX_candles_{start_day}_{end_day}.csv", candles.to_csv(index=False).encode("utf-8-sig"))
            zf.writestr(f"EGX_report_{start_day}_{end_day}.csv", report_csv)
            zf.writestr("EGX_source_raw_response.csv", raw_csv)
            zf.writestr("run_info.json", json.dumps({"run_id": run_id, "status": run_info.get("status"),
                                                       "requested_tickers": len(symbols),
                                                       "returned_raw_items": len(raw_items),
                                                       "valid_candles": len(candles),
                                                       "from": start_day.isoformat(), "to": end_day.isoformat()},
                                                      ensure_ascii=False, indent=2))
        st.download_button("📦 تحميل الحزمة الكاملة ZIP", package.getvalue(),
                           file_name=f"EGX_Candle_Package_{start_day}_{end_day}.zip",
                           mime="application/zip", use_container_width=True)
        if malformed:
            with st.expander("سجلات لم يستطع البرنامج قراءتها"):
                st.dataframe(pd.DataFrame(malformed), use_container_width=True, hide_index=True)
    except requests.Timeout:
        st.error("انتهت مهلة الاتصال بمصدر البيانات. جرّب مرة أخرى أو قلّل عدد الأسهم/الفترة.")
    except Exception as exc:
        st.error(f"فشل التحميل: {exc}")

st.divider()
st.markdown("**إعداد Streamlit Secrets**")
st.code('APIFY_API_TOKEN = "ضع_توكن_Apify_هنا"', language="toml")
st.caption("لا تضع التوكن داخل GitHub أو داخل الكود. لم يتم اختبار تشغيل Actor فعليًا لعدم توفر توكن حسابك؛ تم تجهيز الاتصال وفق واجهة Apify المعلنة.")
