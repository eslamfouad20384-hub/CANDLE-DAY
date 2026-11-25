
import streamlit as st
import pandas as pd
import requests
import time
import io
import zipfile
from datetime import datetime, timezone

# ==========================================
# EGX DAILY CANDLE DOWNLOADER
# DataCedar API V3
# ==========================================

st.set_page_config(
    page_title="EGX Daily Candle Downloader",
    page_icon="📈",
    layout="wide"
)

API_BASE = "https://api.datacedar.com/v3"
EXCHANGE = "egx"

st.title("📈 EGX Daily Candle Downloader")
st.caption("تحميل الشموع اليومية لأسهم البورصة المصرية")

# ==========================================
# SECRET
# ==========================================

try:
    API_KEY = st.secrets["DATACEDAR_API_KEY"]
except Exception:
    API_KEY = ""

if not API_KEY:
    st.error(
        "مفتاح DataCedar غير موجود. "
        "أضفه في Streamlit Settings → Secrets."
    )
    st.stop()

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "X-API-Key": API_KEY,
    "Accept": "application/json"
}

# ==========================================
# HTTP
# ==========================================

def api_get(url, params=None):
    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=60
    )

    if response.status_code != 200:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:1000]

        raise Exception(
            f"HTTP {response.status_code}: {detail}"
        )

    return response.json()


def extract_data(result):
    data = result.get("data", [])

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        return data

    return []


# ==========================================
# SYMBOLS
# ==========================================

def normalize_symbol(value):
    value = str(value or "").strip().upper()

    if value.endswith(".CA"):
        value = value[:-3]

    return value


def load_symbols(uploaded_file, pasted_text):
    raw = ""

    if uploaded_file:
        raw = uploaded_file.getvalue().decode(
            "utf-8-sig", errors="ignore"
        )
    elif pasted_text.strip():
        raw = pasted_text
    else:
        try:
            with open("symbols.txt", encoding="utf-8-sig") as f:
                raw = f.read()
        except FileNotFoundError:
            raw = ""

    symbols = []

    for line in raw.replace(",", "\n").splitlines():
        item = normalize_symbol(line)

        if item and item not in symbols:
            symbols.append(item)

    return symbols


# ==========================================
# ASSET REGISTRY
# ==========================================

def load_registry():
    url = f"{API_BASE}/stocks/assets"

    assets = []
    cursor = None

    while True:
        params = {
            "exchange": EXCHANGE,
            "limit": 1000
        }

        if cursor:
            params["cursor"] = cursor

        result = api_get(url, params)
        data = extract_data(result)

        if isinstance(data, list):
            assets.extend(data)

        pagination = result.get("pagination", {})
        cursor = pagination.get("next_cursor")

        if not cursor:
            break

    return assets


def asset_symbol(asset):
    return normalize_symbol(
        asset.get("symbol")
        or asset.get("ticker")
        or ""
    )


def asset_sid(asset):
    return str(
        asset.get("sid")
        or ""
    ).strip()


def resolve_from_registry(symbol, registry):
    target = normalize_symbol(symbol)

    for asset in registry:
        candidates = [
            asset.get("symbol"),
            asset.get("ticker"),
            asset.get("sid")
        ]

        for candidate in candidates:
            candidate = normalize_symbol(candidate)

            if candidate == target:
                return asset

    return None


# ==========================================
# SEARCH SYMBOL
# ==========================================

def search_symbol(symbol):
    url = f"{API_BASE}/search"

    result = api_get(
        url,
        params={
            "q": symbol,
            "exchange": EXCHANGE,
            "limit": 10
        }
    )

    data = extract_data(result)

    if not isinstance(data, list):
        return None

    target = normalize_symbol(symbol)

    for asset in data:
        candidates = [
            asset.get("symbol"),
            asset.get("ticker")
        ]

        for candidate in candidates:
            if normalize_symbol(candidate) == target:
                return asset

    # لا نختار نتيجة تقريبية بشكل تلقائي
    return None


# ==========================================
# DAILY BARS
# ==========================================

def get_bars_url(asset):
    bars_url = asset.get("bars_url")

    if bars_url:
        if bars_url.startswith("http"):
            return bars_url

        return (
            "https://api.datacedar.com/"
            + bars_url.lstrip("/")
        )

    sid = asset_sid(asset)

    if sid:
        return f"{API_BASE}/stocks/{sid}/bars"

    raise Exception("السهم لا يحتوي على bars_url أو sid")


def download_bars(asset, requested_symbol):
    url = get_bars_url(asset)

    all_rows = []
    cursor = None

    while True:
        params = {
            "timeframe": "1d",
            "order": "asc",
            "limit": 50000,
            "adjustment": "raw"
        }

        if cursor:
            params["cursor"] = cursor

        result = api_get(url, params)
        data = extract_data(result)

        if isinstance(data, list):
            all_rows.extend(data)

        pagination = result.get("pagination", {})
        cursor = pagination.get("next_cursor")

        if not cursor:
            break

    if not all_rows:
        raise Exception("لا توجد شموع متاحة لهذا السهم")

    df = pd.DataFrame(all_rows)

    if "ts_ms" in df.columns:
        timestamps = pd.to_numeric(
            df["ts_ms"], errors="coerce"
        )

        df["date"] = pd.to_datetime(
            timestamps,
            unit="ms",
            utc=True,
            errors="coerce"
        ).dt.tz_convert("Africa/Cairo").dt.strftime("%Y-%m-%d")

    elif "date" in df.columns:
        df["date"] = pd.to_datetime(
            df["date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")

    elif "timestamp" in df.columns:
        df["date"] = pd.to_datetime(
            df["timestamp"],
            utc=True,
            errors="coerce"
        ).dt.tz_convert("Africa/Cairo").dt.strftime("%Y-%m-%d")

    else:
        raise Exception(
            "حقل التاريخ غير موجود في استجابة DataCedar"
        )

    aliases = {
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume"
    }

    df.rename(
        columns={
            old: new
            for old, new in aliases.items()
            if old in df.columns and new not in df.columns
        },
        inplace=True
    )

    required = [
        "date", "open", "high", "low", "close"
    ]

    missing = [
        col for col in required
        if col not in df.columns
    ]

    if missing:
        raise Exception(
            "حقول ناقصة: " + ", ".join(missing)
        )

    if "volume" not in df.columns:
        df["volume"] = 0

    for col in [
        "open", "high", "low", "close", "volume"
    ]:
        df[col] = pd.to_numeric(
            df[col], errors="coerce"
        )

    df["symbol"] = normalize_symbol(requested_symbol)
    df["sid"] = asset_sid(asset)

    df.dropna(
        subset=["date", "open", "high", "low", "close"],
        inplace=True
    )

    df.drop_duplicates(
        subset=["symbol", "date"],
        inplace=True
    )

    df.sort_values(
        ["symbol", "date"],
        inplace=True
    )

    columns = [
        "symbol", "sid", "date",
        "open", "high", "low", "close", "volume"
    ]

    return df[columns]


# ==========================================
# INTERFACE
# ==========================================

st.sidebar.header("إعدادات التحميل")

uploaded_file = st.sidebar.file_uploader(
    "تحميل قائمة الأسهم",
    type=["txt", "csv"]
)

pasted_symbols = st.sidebar.text_area(
    "أو الصق رموز الأسهم هنا",
    height=180,
    placeholder="COMI\nMFPC\nPHDC\nORAS"
)

symbols = load_symbols(
    uploaded_file,
    pasted_symbols
)

st.sidebar.metric(
    "عدد الأسهم المطلوبة",
    len(symbols)
)

delay = st.sidebar.number_input(
    "الفاصل بين طلبات البحث بالثواني",
    min_value=0.0,
    max_value=5.0,
    value=1.1,
    step=0.1
)

st.info(
    "المفتاح يتم قراءته تلقائيًا من Streamlit Secrets. "
    "مش محتاج تدخله في الواجهة."
)

# ==========================================
# LOAD REGISTRY
# ==========================================

if st.button("1️⃣ جلب سجل أصول EGX", type="primary"):

    try:
        with st.spinner("جاري جلب سجل الأصول..."):
            registry = load_registry()

        st.session_state["registry"] = registry

        st.success(
            f"تم تحميل سجل الأصول: {len(registry)} سجل"
        )

        registry_df = pd.DataFrame(registry)

        st.dataframe(
            registry_df,
            use_container_width=True
        )

    except Exception as e:
        st.error(f"فشل جلب سجل الأصول: {e}")


# ==========================================
# DOWNLOAD
# ==========================================

if "registry" in st.session_state:

    registry = st.session_state["registry"]

    st.subheader("2️⃣ تحميل الشموع اليومية")

    if not symbols:
        st.warning(
            "ارفع symbols.txt أو الصق رموز الأسهم في الشريط الجانبي."
        )

    if st.button(
        "ابدأ تحميل الشموع",
        disabled=not symbols
    ):

        downloaded = []
        report = []

        progress = st.progress(0)
        status = st.empty()

        total = len(symbols)

        for index, symbol in enumerate(symbols, 1):

            status.write(
                f"جاري معالجة {symbol} — {index}/{total}"
            )

            try:
                asset = resolve_from_registry(
                    symbol, registry
                )

                if asset is None:
                    asset = search_symbol(symbol)
                    time.sleep(float(delay))

                if asset is None:
                    raise Exception(
                        "لم يتم العثور على تطابق مؤكد للرمز"
                    )

                df = download_bars(asset, symbol)

                downloaded.append(df)

                report.append({
                    "symbol": symbol,
                    "sid": asset_sid(asset),
                    "status": "نجاح",
                    "candles": len(df),
                    "first_date": df["date"].min(),
                    "last_date": df["date"].max(),
                    "details": "تم التحميل"
                })

            except Exception as e:
                report.append({
                    "symbol": symbol,
                    "sid": "",
                    "status": "فشل",
                    "candles": 0,
                    "first_date": "",
                    "last_date": "",
                    "details": str(e)[:500]
                })

            progress.progress(index / total)

        if downloaded:
            all_candles = pd.concat(
                downloaded,
                ignore_index=True
            )
        else:
            all_candles = pd.DataFrame(
                columns=[
                    "symbol", "sid", "date",
                    "open", "high", "low",
                    "close", "volume"
                ]
            )

        report_df = pd.DataFrame(report)

        st.session_state["candles"] = all_candles
        st.session_state["report"] = report_df

        status.success("اكتمل التحميل")


# ==========================================
# RESULTS
# ==========================================

if "report" in st.session_state:

    candles = st.session_state["candles"]
    report = st.session_state["report"]

    st.divider()
    st.subheader("📊 نتائج التحميل")

    success_count = int(
        (report["status"] == "نجاح").sum()
    )

    failed_count = int(
        (report["status"] == "فشل").sum()
    )

    col1, col2, col3 = st.columns(3)

    col1.metric("الأسهم الناجحة", success_count)
    col2.metric("الأسهم الفاشلة", failed_count)
    col3.metric("إجمالي الشموع", len(candles))

    st.dataframe(
        report,
        use_container_width=True
    )

    st.subheader("معاينة الشموع")

    st.dataframe(
        candles.tail(100),
        use_container_width=True
    )

    # CSV
    csv_data = candles.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        "⬇️ تحميل الشموع CSV",
        data=csv_data,
        file_name="EGX_DAILY_CANDLES.csv",
        mime="text/csv"
    )

    # REPORT
    report_csv = report.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        "⬇️ تحميل تقرير الأسهم",
        data=report_csv,
        file_name="EGX_DOWNLOAD_REPORT.csv",
        mime="text/csv"
    )

    # ZIP
    buffer = io.BytesIO()

    with zipfile.ZipFile(
        buffer,
        "w",
        zipfile.ZIP_DEFLATED
    ) as z:

        z.writestr(
            "EGX_DAILY_CANDLES.csv",
            csv_data
        )

        z.writestr(
            "EGX_DOWNLOAD_REPORT.csv",
            report_csv
        )

        z.writestr(
            "DOWNLOAD_INFO.txt",
            (
                "EGX Daily Candle Downloader\n"
                f"Generated: {datetime.now().isoformat()}\n"
                f"Successful symbols: {success_count}\n"
                f"Failed symbols: {failed_count}\n"
                f"Total candles: {len(candles)}\n"
            )
        )

    st.download_button(
        "📦 تحميل كل البيانات ZIP",
        data=buffer.getvalue(),
        file_name="EGX_DAILY_DATA_PACKAGE.zip",
        mime="application/zip"
    )
