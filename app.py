import io
import os
import time
import zipfile
from datetime import date, timedelta
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

HEADERS = {"Accept": "application/json", "User-Agent": "EGX-Borsa-Candle-Downloader/1.0"}

st.set_page_config(page_title="EGX Daily Candle Downloader - Borsa", page_icon="📈", layout="wide")
st.title("📈 EGX Daily Candle Downloader — Borsa API")
st.caption("تحميل الشموع اليومية للأسهم المصرية خلال فترة زمنية تختارها، مع تقرير تغطية وتصدير CSV / ZIP.")

@st.cache_data(ttl=3600, show_spinner=False)
def load_symbols():
    path = "symbols.txt"
    if not os.path.exists(path):
        raise FileNotFoundError("ملف symbols.txt غير موجود بجوار app.py على GitHub")
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            s = line.strip().upper()
            if s and not s.startswith("#"):
                s = s.replace(".CA", "")
                if s not in out:
                    out.append(s)
    return out


def get_secret(name, default=""):
    try:
        return str(st.secrets.get(name, default) or default).strip()
    except Exception:
        return default


def extract_records(payload):
    """Accept common Borsa response envelopes without assuming one wrapper."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "candles", "history", "results", "items", "bars"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for nested in ("data", "candles", "history", "results", "items", "bars"):
                if isinstance(value.get(nested), list):
                    return value[nested]
    return []


def normalize_candle(row, symbol, source):
    if not isinstance(row, dict):
        return None
    date_value = row.get("date") or row.get("timestamp") or row.get("datetime") or row.get("time") or row.get("t")
    if date_value is None:
        return None
    try:
        day = pd.to_datetime(date_value, utc=True).date()
    except Exception:
        try:
            day = pd.to_datetime(date_value).date()
        except Exception:
            return None

    aliases = {
        "open": ("open", "o", "Open"),
        "high": ("high", "h", "High"),
        "low": ("low", "l", "Low"),
        "close": ("close", "c", "Close", "adj_close", "adjusted_close"),
        "volume": ("volume", "v", "Volume"),
    }
    values = {}
    for field, names in aliases.items():
        val = next((row.get(k) for k in names if row.get(k) is not None), None)
        if val is None:
            return None
        try:
            values[field] = float(val)
        except Exception:
            return None
    return {"symbol": f"{symbol}.CA", "date": day.isoformat(), **values,
            "source": source, "provider_symbol": row.get("symbol") or symbol}


def fetch_history(base_url, symbol, start_day, end_day, timeout=45):
    # Borsa historical route; Borsa's provider adapters handle ticker variants.
    url = f"{base_url.rstrip('/')}/v1/historical/{quote(symbol, safe='')}"
    params = {"date_from": start_day.isoformat(), "date_to": end_day.isoformat(), "period": "Year"}
    response = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    if response.status_code != 200:
        detail = response.text[:350].replace("\n", " ")
        raise RuntimeError(f"HTTP {response.status_code}: {detail}")
    payload = response.json()
    records = extract_records(payload)
    if not records and isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(str(payload.get("error"))[:250])
    return records, response.headers.get("X-Data-Source", "Borsa API"), payload


with st.sidebar:
    st.header("إعدادات التحميل")
    default_url = get_secret("BORSA_API_URL", "")
    borsa_url = st.text_input("رابط Borsa API المستضاف", value=default_url,
                              placeholder="https://your-borsa-service.example.com",
                              help="لازم يكون عنوان خدمة Borsa المنشورة، مش رابط GitHub.")
    start_date = st.date_input("من تاريخ", value=date.today() - timedelta(days=365), max_value=date.today(), key="start_date")
    end_date = st.date_input("إلى تاريخ", value=date.today() - timedelta(days=1), max_value=date.today(), key="end_date")
    delay = st.number_input("الفاصل بين الأسهم (ثانية)", min_value=0.0, max_value=5.0, value=0.2, step=0.1)
    timeout = st.number_input("مهلة الطلب (ثانية)", min_value=10, max_value=120, value=45, step=5)

try:
    symbols = load_symbols()
except Exception as exc:
    st.error(str(exc))
    st.stop()

st.metric("عدد الأسهم في القائمة", len(symbols))
st.info("ضع ملف symbols.txt بجوار app.py في مستودع GitHub. التطبيق يرسل رموز التداول مثل COMI مباشرة إلى Borsa.")
st.warning("مهم: Borsa برنامج API ذاتي الاستضافة. لازم تنشر خدمة Borsa مستقلة وتضع رابطها هنا أو في Streamlit Secrets باسم BORSA_API_URL. رابط العرض العام التجريبي لا يضمن دعم الشموع التاريخية.")

if start_date > end_date:
    st.error("تاريخ البداية لازم يكون قبل تاريخ النهاية أو مساوي له.")

if st.button("📥 تحميل الشموع اليومية للفترة المحددة", type="primary", use_container_width=True):
    if not borsa_url.strip():
        st.error("أدخل رابط خدمة Borsa API المنشورة أولًا.")
        st.stop()
    if start_date > end_date:
        st.error("راجع التاريخين: البداية أكبر من النهاية.")
        st.stop()

    # Check service before spending requests on the whole universe.
    try:
        health_url = borsa_url.rstrip("/") + "/v1/health"
        health = requests.get(health_url, headers=HEADERS, timeout=15)
        if health.status_code >= 400:
            st.warning(f"فحص /v1/health رجع HTTP {health.status_code}. هنجرب مسار التاريخية مباشرة.")
        else:
            st.success("تم الوصول إلى خدمة Borsa.")
    except Exception as exc:
        st.warning(f"تعذر فحص الصحة: {exc}. هنكمل اختبار التاريخية مباشرة.")

    candle_rows, report_rows = [], []
    progress = st.progress(0)
    status = st.empty()
    for index, symbol in enumerate(symbols, start=1):
        status.write(f"تحميل {index}/{len(symbols)} — {symbol}")
        try:
            records, source_header, raw = fetch_history(borsa_url, symbol, start_date, end_date, int(timeout))
            normalized = []
            for item in records:
                candle = normalize_candle(item, symbol, source_header or "Borsa API")
                if candle and start_date <= date.fromisoformat(candle["date"]) <= end_date:
                    normalized.append(candle)
            # Remove duplicate dates per ticker, keep last provider record.
            dedup = {x["date"]: x for x in normalized}
            normalized = [dedup[d] for d in sorted(dedup)]
            candle_rows.extend(normalized)
            if normalized:
                report_rows.append({"symbol": symbol + ".CA", "status": "نجاح", "candles": len(normalized),
                                    "from": normalized[0]["date"], "to": normalized[-1]["date"],
                                    "source": source_header or "Borsa API", "message": "تم استلام OHLCV والتحقق من الفترة"})
            else:
                report_rows.append({"symbol": symbol + ".CA", "status": "لا توجد بيانات", "candles": 0,
                                    "from": "", "to": "", "source": source_header or "Borsa API",
                                    "message": "الاستجابة لا تحتوي شموع OHLCV مكتملة داخل الفترة"})
        except Exception as exc:
            report_rows.append({"symbol": symbol + ".CA", "status": "خطأ", "candles": 0,
                                "from": "", "to": "", "source": "", "message": str(exc)[:350]})
        progress.progress(index / len(symbols))
        if delay and index < len(symbols):
            time.sleep(float(delay))

    status.empty()
    candles = pd.DataFrame(candle_rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "source", "provider_symbol"])
    report = pd.DataFrame(report_rows)
    if not candles.empty:
        candles = candles.sort_values(["symbol", "date"]).reset_index(drop=True)

    successful = sum(1 for x in report_rows if x["status"] == "نجاح")
    st.success(f"انتهى التحميل: {len(candles):,} شمعة من {successful} سهم خلال الفترة {start_date} إلى {end_date}.")
    c1, c2, c3 = st.columns(3)
    c1.metric("إجمالي الشموع", f"{len(candles):,}")
    c2.metric("أسهم بها بيانات", successful)
    c3.metric("نسبة تغطية الأسهم", f"{successful / len(symbols) * 100:.1f}%")

    st.subheader("الشموع اليومية")
    st.dataframe(candles, use_container_width=True, hide_index=True)
    st.subheader("تقرير كل الأسهم")
    st.dataframe(report, use_container_width=True, hide_index=True)

    csv_candles = candles.to_csv(index=False).encode("utf-8-sig")
    csv_report = report.to_csv(index=False).encode("utf-8-sig")
    mapping = pd.DataFrame({"symbol": [s + ".CA" for s in symbols], "borsa_symbol": symbols})
    csv_mapping = mapping.to_csv(index=False).encode("utf-8-sig")
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"EGX_daily_candles_{start_date}_{end_date}.csv", csv_candles)
        archive.writestr(f"EGX_download_report_{start_date}_{end_date}.csv", csv_report)
        archive.writestr("EGX_Borsa_symbol_mapping.csv", csv_mapping)
    st.download_button("⬇️ تحميل الشموع CSV", csv_candles,
                       file_name=f"EGX_daily_candles_{start_date}_{end_date}.csv", mime="text/csv")
    st.download_button("⬇️ تحميل تقرير الأسهم CSV", csv_report,
                       file_name=f"EGX_download_report_{start_date}_{end_date}.csv", mime="text/csv")
    st.download_button("📦 تحميل الحزمة ZIP", bundle.getvalue(),
                       file_name=f"EGX_Borsa_Package_{start_date}_{end_date}.zip", mime="application/zip")

st.divider()
st.caption("تنبيه: لا يتم اعتبار السهم ناجحًا إلا بعد استلام شموع بتاريخ وOHLCV مكتملة. جودة التغطية مرتبطة بمزودي البيانات ومفاتيحهم المفعّلة داخل خدمة Borsa.")
