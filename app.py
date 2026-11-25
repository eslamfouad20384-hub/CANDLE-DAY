
# app.py
import streamlit as st
import pandas as pd
import requests
import json
import time
import io
import zipfile
from datetime import datetime

st.set_page_config(
    page_title="EGX Daily Candle Downloader",
    page_icon="📈",
    layout="wide"
)

st.title("📈 EGX Daily Candle Downloader")
st.caption("تحميل الشموع اليومية لأسهم البورصة المصرية من DataCedar")

API_BASE = "https://api.datacedar.com"

def headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "X-API-Key": api_key,
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

def get_json(url, api_key, params=None):
    response = requests.get(
        url,
        headers=headers(api_key),
        params=params,
        timeout=30
    )
    response.raise_for_status()
    return response.json()

def extract_rows(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("data", "results", "bars", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value

    return []

def load_symbols(uploaded):
    if uploaded:
        content = uploaded.getvalue().decode("utf-8-sig")
        return list(dict.fromkeys(
            x.strip().upper()
            for x in content.replace(",", "\n").splitlines()
            if x.strip()
        ))

    try:
        with open("symbols.txt", encoding="utf-8") as f:
            return list(dict.fromkeys(
                x.strip().upper()
                for x in f.read().replace(",", "\n").splitlines()
                if x.strip()
            ))
    except FileNotFoundError:
        return []

def find_symbol(row):
    return str(
        row.get("symbol")
        or row.get("ticker")
        or row.get("code")
        or ""
    ).upper().strip()

def download_registry(api_key):
    # المسار ده لازم يتوافق مع مسار سجل الأصول في حساب DataCedar.
    url = f"{API_BASE}/v3/stocks"
    data = get_json(url, api_key)
    return extract_rows(data), data

def get_bars(api_key, asset):
    bars_url = asset.get("bars_url")

    if not bars_url:
        return None, "لا يوجد bars_url في سجل السهم"

    if bars_url.startswith("http"):
        url = bars_url
    else:
        url = API_BASE.rstrip("/") + "/" + bars_url.lstrip("/")

    params = {
        "timeframe": "1Day",
        "limit": 1000
    }

    data = get_json(url, api_key, params=params)
    rows = extract_rows(data)

    if not rows:
        return None, "الـ API رجع بدون شموع"

    df = pd.DataFrame(rows)

    # توحيد أسماء الأعمدة الشائعة
    aliases = {
        "timestamp": "date",
        "t": "date",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume"
    }

    df.rename(columns={
        col: aliases[col]
        for col in df.columns
        if col in aliases
    }, inplace=True)

    required = ["date", "open", "high", "low", "close"]

    if not all(col in df.columns for col in required):
        return None, "حقول الشمعة غير مكتملة: " + ", ".join(df.columns)

    if "volume" not in df.columns:
        df["volume"] = 0

    df["symbol"] = find_symbol(asset)

    df["date"] = pd.to_datetime(
        df["date"], errors="coerce", utc=True
    ).dt.strftime("%Y-%m-%d")

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df.dropna(subset=["date", "open", "high", "low", "close"], inplace=True)
    df = df[[
        "symbol", "date", "open", "high",
        "low", "close", "volume"
    ]]

    df.drop_duplicates(subset=["symbol", "date"], inplace=True)
    df.sort_values("date", inplace=True)

    return df, None


with st.sidebar:
    st.header("إعدادات الاتصال")
    api_key = st.text_input(
        "DataCedar API Key",
        type="password"
    )

    uploaded_symbols = st.file_uploader(
        "تحميل قائمة الأسهم TXT أو CSV",
        type=["txt", "csv"]
    )

    limit = st.number_input(
        "عدد الأسهم المراد تحميلها",
        min_value=1,
        max_value=300,
        value=201
    )

    delay = st.number_input(
        "فاصل زمني بين الطلبات بالثواني",
        min_value=0.0,
        max_value=5.0,
        value=0.2
    )

symbols = load_symbols(uploaded_symbols)

st.write(f"عدد الرموز الموجودة: **{len(symbols)}**")

if st.button("1 - جلب سجل الأسهم من DataCedar", disabled=not api_key):
    try:
        with st.spinner("جاري جلب سجل الأصول..."):
            registry, raw_registry = download_registry(api_key)

        st.session_state["registry"] = registry
        st.session_state["raw_registry"] = raw_registry

        st.success(f"تم جلب {len(registry)} سجل")
        st.dataframe(pd.DataFrame(registry), use_container_width=True)

    except Exception as e:
        st.error(f"فشل جلب سجل الأصول: {e}")

if "registry" in st.session_state:
    registry = st.session_state["registry"]

    st.subheader("مطابقة الأسهم")
    registry_df = pd.DataFrame(registry)

    if not registry_df.empty:
        registry_df["match_symbol"] = registry_df.apply(find_symbol, axis=1)
        matched = registry_df[
            registry_df["match_symbol"].isin(symbols)
        ]

        st.write(f"الأسهم المطابقة: **{len(matched)}**")

        if st.button("2 - ابدأ تحميل الشموع اليومية"):
            results = []
            reports = []

            progress = st.progress(0)
            status = st.empty()

            assets = matched.head(int(limit)).to_dict("records")
            total = len(assets)

            for i, asset in enumerate(assets, 1):
                symbol = find_symbol(asset)
                status.write(f"تحميل {symbol} — {i}/{total}")

                try:
                    df, error = get_bars(api_key, asset)

                    if error:
                        reports.append({
                            "symbol": symbol,
                            "status": "فشل",
                            "details": error
                        })
                    else:
                        results.append(df)
                        reports.append({
                            "symbol": symbol,
                            "status": "نجاح",
                            "candles": len(df),
                            "details": "تم التحميل"
                        })

                except Exception as e:
                    reports.append({
                        "symbol": symbol,
                        "status": "فشل",
                        "details": str(e)[:300]
                    })

                progress.progress(i / max(total, 1))
                time.sleep(float(delay))

            all_data = (
                pd.concat(results, ignore_index=True)
                if results else pd.DataFrame()
            )

            report_df = pd.DataFrame(reports)

            st.session_state["candles"] = all_data
            st.session_state["report"] = report_df

            st.success(
                f"اكتمل التحميل: {len(results)} سهم نجح "
                f"من أصل {total}"
            )

if "candles" in st.session_state:
    candles = st.session_state["candles"]
    report = st.session_state["report"]

    st.subheader("نتيجة التحميل")

    c1, c2, c3 = st.columns(3)
    c1.metric("عدد الأسهم الناجحة", int(
        (report["status"] == "نجاح").sum()
    ))
    c2.metric("عدد الشموع", len(candles))
    c3.metric("عدد الأسهم الفاشلة", int(
        (report["status"] == "فشل").sum()
    ))

    st.dataframe(report, use_container_width=True)

    st.download_button(
        "تحميل كل الشموع CSV",
        candles.to_csv(index=False).encode("utf-8-sig"),
        file_name="EGX_DAILY_CANDLES.csv",
        mime="text/csv"
    )

    st.download_button(
        "تحميل تقرير الأسهم CSV",
        report.to_csv(index=False).encode("utf-8-sig"),
        file_name="EGX_DOWNLOAD_REPORT.csv",
        mime="text/csv"
    )

    if st.button("تجهيز ZIP بكل الملفات"):
        buffer = io.BytesIO()

        with zipfile.ZipFile(
            buffer, "w", zipfile.ZIP_DEFLATED
        ) as z:
            z.writestr(
                "EGX_DAILY_CANDLES.csv",
                candles.to_csv(index=False)
            )
            z.writestr(
                "EGX_DOWNLOAD_REPORT.csv",
                report.to_csv(index=False)
            )

            if "raw_registry" in st.session_state:
                z.writestr(
                    "DATACEDAR_REGISTRY.json",
                    json.dumps(
                        st.session_state["raw_registry"],
                        ensure_ascii=False,
                        indent=2
                    )
                )

        st.download_button(
            "⬇️ تنزيل ملف ZIP",
            buffer.getvalue(),
            file_name="EGX_DAILY_DATA_PACKAGE.zip",
            mime="application/zip"
        )
