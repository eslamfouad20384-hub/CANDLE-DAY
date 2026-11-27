
import io
import time
import zipfile
from datetime import date, timedelta

import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(
    page_title="EGX Daily Candle Downloader",
    page_icon="📈",
    layout="wide"
)

st.title("EGX FREE DAILY CANDLE DOWNLOADER")
st.caption("برنامج مستقل لجلب الشموع اليومية بدون Apify أو API Key")

st.warning(
    "Yahoo Finance مصدر غير رسمي، ومش مضمون إنه يدعم كل الأسهم المصرية. "
    "البرنامج لا ينشئ شموع وهمية."
)

DEFAULT_SYMBOLS = """
COMI
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
OCPH
"""


def parse_symbols(raw):
    result = []
    for line in raw.replace(",", "\n").replace(";", "\n").splitlines():
        symbol = line.strip().upper()
        symbol = symbol.replace(".CA", "").replace(".EY", "")
        if symbol and symbol not in result:
            result.append(symbol)
    return result


def download_stock(symbol, start_date, end_date):
    errors = []

    # تجربة صيغ الرموز المختلفة
    for ticker in [
        symbol + ".CA",
        symbol + ".EY",
        symbol
    ]:
        try:
            df = yf.download(
                ticker,
                start=start_date.isoformat(),
                end=(end_date + timedelta(days=1)).isoformat(),
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
                timeout=25
            )

            if df is None or df.empty:
                errors.append(ticker + ": لا توجد بيانات")
                continue

            if isinstance(df.columns, pd.MultiIndex):
                try:
                    df = df.xs(ticker, axis=1, level=-1)
                except Exception:
                    df.columns = [
                        col[0] for col in df.columns
                    ]

            df.columns = [
                str(col).lower().replace(" ", "_")
                for col in df.columns
            ]

            required = [
                "open", "high", "low", "close", "volume"
            ]

            if not all(col in df.columns for col in required):
                errors.append(ticker + ": أعمدة OHLCV ناقصة")
                continue

            for col in required:
                df[col] = pd.to_numeric(
                    df[col], errors="coerce"
                )

            df = df.dropna(subset=required)

            # التحقق من صحة الشمعة
            df = df[
                (df["high"] >= df[["open", "low", "close"]].max(axis=1))
                &
                (df["low"] <= df[["open", "high", "close"]].min(axis=1))
            ]

            if df.empty:
                errors.append(ticker + ": لا توجد شموع سليمة")
                continue

            result = pd.DataFrame({
                "symbol": symbol + ".CA",
                "date": pd.to_datetime(df.index).date.astype(str),
                "open": df["open"].values,
                "high": df["high"].values,
                "low": df["low"].values,
                "close": df["close"].values,
                "volume": df["volume"].values,
                "currency": "EGP",
                "source": "Yahoo Finance: " + ticker
            })

            return result, ticker

        except Exception as error:
            errors.append(ticker + ": " + str(error)[:120])

        time.sleep(0.4)

    raise RuntimeError(" | ".join(errors))


st.sidebar.header("إعدادات التحميل")

today = date.today()

start_date = st.sidebar.date_input(
    "تاريخ البداية",
    today - timedelta(days=365),
    max_value=today
)

end_date = st.sidebar.date_input(
    "تاريخ النهاية",
    today,
    min_value=start_date,
    max_value=today
)

symbols_text = st.text_area(
    "قائمة الأسهم",
    DEFAULT_SYMBOLS,
    height=350
)

manual_file = st.file_uploader(
    "CSV يدوي لاستكمال الأسهم الناقصة (اختياري)",
    type=["csv"]
)

symbols = parse_symbols(symbols_text)

st.metric("عدد الأسهم المطلوبة", len(symbols))

st.info(
    "صيغة CSV اليدوي المطلوبة: "
    "symbol,date,open,high,low,close,volume"
)

if st.button("بدء تحميل الشموع", type="primary", width="stretch"):

    if not symbols:
        st.error("قائمة الأسهم فارغة")
        st.stop()

    progress = st.progress(0)
    status = st.empty()

    frames = []
    report = []

    for index, symbol in enumerate(symbols, start=1):

        status.write(
            f"جاري تحميل {symbol} "
            f"({index}/{len(symbols)})"
        )

        try:
            frame, provider = download_stock(
                symbol,
                start_date,
                end_date
            )

            frames.append(frame)

            report.append({
                "symbol": symbol + ".CA",
                "status": "نجاح",
                "rows": len(frame),
                "provider": provider,
                "details": ""
            })

        except Exception as error:

            report.append({
                "symbol": symbol + ".CA",
                "status": "فشل",
                "rows": 0,
                "provider": "",
                "details": str(error)
            })

        progress.progress(index / len(symbols))
        time.sleep(0.5)

    data = (
        pd.concat(frames, ignore_index=True)
        if frames else pd.DataFrame()
    )

    # استكمال البيانات من CSV يدوي
    if manual_file is not None:

        try:
            manual = pd.read_csv(manual_file)

            manual.columns = [
                str(col).strip().lower().replace(" ", "_")
                for col in manual.columns
            ]

            required = [
                "symbol", "date", "open",
                "high", "low", "close", "volume"
            ]

            if not all(col in manual.columns for col in required):
                raise ValueError(
                    "تأكد من وجود الأعمدة: "
                    + ", ".join(required)
                )

            manual["symbol"] = (
                manual["symbol"].astype(str).str.upper()
                .str.replace(".EY", "", regex=False)
                .str.replace(".CA", "", regex=False)
                + ".CA"
            )

            manual["date"] = pd.to_datetime(
                manual["date"], errors="coerce"
            )

            manual = manual.dropna(subset=["date"])

            manual = manual[
                (manual["date"].dt.date >= start_date)
                & (manual["date"].dt.date <= end_date)
            ]

            for col in [
                "open", "high", "low", "close", "volume"
            ]:
                manual[col] = pd.to_numeric(
                    manual[col], errors="coerce"
                )

            manual = manual.dropna(
                subset=[
                    "open", "high", "low", "close", "volume"
                ]
            )

            manual["date"] = manual["date"].dt.date.astype(str)
            manual["currency"] = "EGP"
            manual["source"] = "Manual CSV"

            if "adj_close" not in manual.columns:
                manual["adj_close"] = manual["close"]

            if data.empty:
                data = manual
            else:
                data = pd.concat(
                    [data, manual],
                    ignore_index=True
                )

        except Exception as error:
            st.error("خطأ في CSV اليدوي: " + str(error))

    if not data.empty:

        # البيانات اليدوية لها الأولوية عند تكرار الرمز والتاريخ
        data = data.drop_duplicates(
            subset=["symbol", "date"],
            keep="last"
        )

        data = data.sort_values(
            ["symbol", "date"]
        )

    report_df = pd.DataFrame(report)

    st.subheader("ملخص التحميل")

    col1, col2, col3 = st.columns(3)

    col1.metric(
        "عدد الشموع",
        len(data)
    )

    col2.metric(
        "أسهم لها بيانات",
        data["symbol"].nunique() if not data.empty else 0
    )

    col3.metric(
        "عدد الأسهم المطلوبة",
        len(symbols)
    )

    st.subheader("تقرير الأسهم")

    st.dataframe(
        report_df,
        width="stretch",
        hide_index=True
    )

    st.subheader("بيانات الشموع")

    if data.empty:

        st.error(
            "لم يتم العثور على بيانات صالحة. "
            "لم يتم إنشاء بيانات بديلة."
        )

    else:

        st.dataframe(
            data,
            width="stretch",
            hide_index=True
        )

        csv_bytes = data.to_csv(
            index=False
        ).encode("utf-8-sig")

        st.download_button(
            "تحميل ملف CSV",
            csv_bytes,
            "EGX_daily_candles.csv",
            "text/csv",
            width="stretch"
        )

    # إنشاء ZIP
    memory = io.BytesIO()

    with zipfile.ZipFile(
        memory, "w", zipfile.ZIP_DEFLATED
    ) as archive:

        archive.writestr(
            "EGX_daily_candles.csv",
            data.to_csv(index=False).encode("utf-8-sig")
            if not data.empty else ""
        )

        archive.writestr(
            "EGX_download_report.csv",
            report_df.to_csv(index=False).encode("utf-8-sig")
        )

        archive.writestr(
            "README.txt",
            "Independent EGX Daily Candle Downloader. "
            "Yahoo Finance is unofficial and coverage is not guaranteed. "
            "No synthetic candles are generated."
        )

    st.download_button(
        "تحميل ملف ZIP الكامل",
        memory.getvalue(),
        "EGX_Daily_Candles.zip",
        "application/zip",
        width="stretch"
    )

st.caption(
    "ملاحظة: Yahoo Finance قد لا يدعم كل الأسهم المصرية. "
    "لا تعتبر عدم ظهور البيانات دليلاً على عدم وجود تداول."
)
