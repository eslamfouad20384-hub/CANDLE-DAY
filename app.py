import io, os, time, zipfile
from datetime import date, datetime, timedelta, timezone
import pandas as pd
import requests
import streamlit as st

BASE = 'https://api.datacedar.com/v3'
HEADERS_BASE = {'Accept': 'application/json', 'User-Agent': 'EGX-Candle-Downloader/1.0'}

st.set_page_config(page_title='EGX Daily Candle Downloader', page_icon='📈', layout='wide')
st.title('📈 EGX Daily Candle Downloader')
st.caption('تطبيق مستقل لجلب الشموع اليومية لأسهم قائمتك وتصديرها CSV / ZIP')

@st.cache_data(ttl=3600, show_spinner=False)
def load_symbols():
    with open('symbols.txt', encoding='utf-8') as f:
        return [x.strip().upper() for x in f if x.strip()]

def _normalize_symbol(value):
    value = str(value or '').strip().upper()
    if not value:
        return ''
    value = value.replace('.CA', '')
    # DataCedar canonical IDs are exchange:TICKER (for example egx:COMI).
    if ':' in value:
        value = value.rsplit(':', 1)[-1]
    return value.strip()

@st.cache_data(ttl=3600, show_spinner=False)
def get_egx_registry(_key):
    # DataCedar documents both X-API-Key and Bearer authentication.
    headers = {**HEADERS_BASE, 'X-API-Key': _key, 'Authorization': f'Bearer {_key}'}
    rows, cursor, page_count, envelope_keys = [], None, 0, []
    for _ in range(50):
        params = {'exchange': 'egx', 'limit': 1000}
        if cursor:
            params['cursor'] = cursor
        r = requests.get(f'{BASE}/stocks/assets', headers=headers, params=params, timeout=30)
        if r.status_code != 200:
            try:
                err = r.json().get('error', {})
                detail = err.get('message') or err.get('code') or r.text[:400]
            except Exception:
                detail = r.text[:400]
            raise RuntimeError(f'تعذر تحميل سجل الرموز ({r.status_code}): {detail}')
        j = r.json()
        if not isinstance(j, dict):
            raise RuntimeError(f'استجابة سجل DataCedar غير متوقعة: النوع {type(j).__name__}')
        envelope_keys = list(j.keys())
        data = j.get('data', [])
        if isinstance(data, dict):
            # Accommodate either a keyed registry or an object containing asset rows.
            nested = data.get('assets') or data.get('items') or data.get('results')
            data = nested if isinstance(nested, list) else list(data.values())
        if not isinstance(data, list):
            raise RuntimeError(f'حقل data في سجل DataCedar ليس قائمة/قاموسًا؛ الحقول: {envelope_keys}')
        rows.extend(x for x in data if isinstance(x, dict))
        page_count += 1
        pg = j.get('pagination') or {}
        cursor = pg.get('next_cursor') if isinstance(pg, dict) else None
        if not cursor:
            break
    mapping = {}
    sample_fields = sorted({k for a in rows[:30] for k in a.keys()})
    for a in rows:
        sid = a.get('sid') or a.get('id') or a.get('symbol_id')
        raw_symbol = (a.get('symbol') or a.get('ticker') or a.get('code') or
                      a.get('exchange_symbol') or a.get('local_symbol') or '')
        sym = _normalize_symbol(raw_symbol)
        # If the payload omits a separate ticker field, derive it from canonical sid.
        if not sym and sid:
            sym = _normalize_symbol(sid)
        if sid and sym:
            mapping[sym] = str(sid)
    diagnostics = {
        'rows': len(rows), 'pages': page_count, 'matched_registry_symbols': len(mapping),
        'response_fields': ', '.join(envelope_keys),
        'asset_fields': ', '.join(sample_fields) if sample_fields else 'لا توجد سجلات',
        'sample_symbols': ', '.join(list(mapping.keys())[:12]) if mapping else 'لا توجد رموز قابلة للاستخراج',
    }
    return mapping, diagnostics

def fetch_batch(key, sids, day, latest=False):
    headers = {**HEADERS_BASE, 'X-API-Key': key, 'Authorization': f'Bearer {key}'}
    params = {'symbols': ','.join(sids), 'timeframe': '1d', 'from': (day-timedelta(days=14)).isoformat() if latest else day.isoformat(), 'to': day.isoformat(), 'adjustment': 'raw', 'order': 'asc', 'limit': 500}
    r = requests.get(f'{BASE}/stocks/bars', headers=headers, params=params, timeout=45)
    if r.status_code != 200:
        try: msg = r.json().get('error', {}).get('message', r.text[:250])
        except Exception: msg = r.text[:250]
        raise RuntimeError(f'HTTP {r.status_code}: {msg}')
    return r.json()

with st.sidebar:
    st.header('إعدادات التحميل')
    try:
        secret_key = str(st.secrets.get('DATACEDAR_API_KEY', '') or st.secrets.get('DATACEDAR_KEY', '')).strip()
    except Exception:
        secret_key = ''
    ui_key = st.text_input('DataCedar API Key (اختياري لو موجود في Secrets)', type='password', value='', help='لو كتبت مفتاح هنا سيُستخدم بدل المفتاح الموجود في Streamlit Secrets.')
    api_key = ui_key.strip() or secret_key
    if secret_key and not ui_key.strip():
        st.caption('تم العثور على مفتاح API في Streamlit Secrets.')
    selected_date = st.date_input('تاريخ جلسة التداول', value=date.today()-timedelta(days=1), max_value=date.today())
    use_latest = st.checkbox('آخر جلسة متاحة بدل التاريخ المحدد', value=False)
    wait_seconds = st.number_input('الفاصل بين الطلبات (ثانية)', min_value=1.0, max_value=10.0, value=1.1, step=0.1)
    st.caption('الخطة المجانية: حتى 3 رموز في الطلب وطلب واحد/ثانية وفق حدود المزود.')

symbols = load_symbols()
st.metric('عدد الأسهم في القائمة', len(symbols))
st.info('القائمة مأخوذة من EGX100 داخل نسخة EGX AI PRO MAX V10.3.3 DAILY ENGINE RECOVERY، بعد إزالة التكرار.')

if st.button('🚀 ابدأ تحميل الشموع', type='primary', use_container_width=True):
    if not api_key.strip():
        st.error('اكتب مفتاح DataCedar في الشريط الجانبي أولًا.')
        st.stop()
    try:
        with st.spinner('بجلب سجل أسهم EGX ومطابقة الرموز...'):
            registry, registry_diag = get_egx_registry(api_key.strip())
        st.subheader('تشخيص سجل DataCedar')
        d1, d2, d3 = st.columns(3)
        d1.metric('سجلات رجعت من المزود', registry_diag['rows'])
        d2.metric('صفحات السجل', registry_diag['pages'])
        d3.metric('رموز قابلة للمطابقة', registry_diag['matched_registry_symbols'])
        with st.expander('تفاصيل الاستجابة والرموز التجريبية'):
            st.write('حقول الاستجابة:', registry_diag['response_fields'])
            st.write('حقول سجل السهم:', registry_diag['asset_fields'])
            st.write('أمثلة الرموز:', registry_diag['sample_symbols'])
        matches = {s: registry[s] for s in symbols if s in registry}
        missing_map = [s for s in symbols if s not in registry]
        if not matches:
            st.error('لم يتم العثور على تطابق. راجع التشخيص أعلاه: إذا كان عدد السجلات صفرًا فقد يكون فلتر EGX أو تغطية البورصة غير متاحين؛ وإذا رجعت سجلات لكن بلا رموز، فشكل بيانات السجل مختلف عن المتوقع.')
            st.info('قائمة التطبيق تحتوي رموزًا بدون .CA، بينما ملف التصدير يضيف .CA فقط للحفاظ على تنسيق EGX AI PRO MAX.')
            st.stop()
        targets = list(matches.items())
        batches = [targets[i:i+3] for i in range(0, len(targets), 3)]
        all_rows, report = [], []
        bar = st.progress(0); status = st.empty()
        effective_day = selected_date
        for bi, batch in enumerate(batches):
            names = [x[0] for x in batch]; sids = [x[1] for x in batch]
            status.write(f'الطلب {bi+1}/{len(batches)} — ' + '، '.join(names))
            try:
                payload = fetch_batch(api_key.strip(), sids, effective_day, use_latest)
                data = payload.get('data', {})
                for sym, sid in batch:
                    series = data.get(sid, []) if isinstance(data, dict) else []
                    row = None
                    candidates = []
                    for item in series or []:
                        ts = item.get('ts_ms')
                        if ts is not None:
                            dt = datetime.fromtimestamp(int(ts)/1000, tz=timezone.utc).date()
                            if dt <= effective_day and (use_latest or dt == effective_day): candidates.append((dt, item))
                    if candidates: row = max(candidates, key=lambda x: x[0])[1]; actual_day = max(candidates, key=lambda x: x[0])[0]
                    else: actual_day = effective_day
                    if row and all(row.get(k) is not None for k in ['open','high','low','close','volume']):
                        all_rows.append({'symbol': sym+'.CA', 'sid': sid, 'date': actual_day.isoformat(), 'open': float(row['open']), 'high': float(row['high']), 'low': float(row['low']), 'close': float(row['close']), 'volume': float(row['volume']), 'adjustment': row.get('adjustment','raw'), 'source':'DataCedar'})
                        report.append({'symbol':sym+'.CA','sid':sid,'status':'نجاح','message':f'شمعة OHLCV مكتملة بتاريخ {actual_day.isoformat()}'})
                    else:
                        report.append({'symbol':sym+'.CA','sid':sid,'status':'غير متاح','message':'لا توجد شمعة مكتملة ضمن النطاق المختار'})
            except Exception as e:
                for sym, sid in batch:
                    report.append({'symbol':sym+'.CA','sid':sid,'status':'خطأ','message':str(e)[:300]})
            bar.progress((bi+1)/len(batches))
            if bi < len(batches)-1: time.sleep(float(wait_seconds))
        for sym in missing_map:
            report.append({'symbol':sym+'.CA','sid':'','status':'غير مطابق','message':'لم يتم العثور على الرمز في سجل DataCedar'})
        candles = pd.DataFrame(all_rows, columns=['symbol','sid','date','open','high','low','close','volume','adjustment','source'])
        rep = pd.DataFrame(report)
        st.success(f'انتهى التحميل: {len(candles)} شمعة مكتملة من أصل {len(symbols)} سهم.')
        c1,c2,c3 = st.columns(3); c1.metric('شموع مكتملة',len(candles)); c2.metric('غير متاحة/أخطاء',len(rep)-len(candles)); c3.metric('نسبة التغطية',f'{len(candles)/len(symbols)*100:.1f}%')
        st.subheader('معاينة الشموع')
        st.dataframe(candles, use_container_width=True, hide_index=True)
        st.subheader('تقرير كل الأسهم')
        st.dataframe(rep, use_container_width=True, hide_index=True)
        csv1 = candles.to_csv(index=False).encode('utf-8-sig')
        csv2 = rep.to_csv(index=False).encode('utf-8-sig')
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr(f'EGX_daily_candles_{effective_day}.csv',csv1)
            z.writestr(f'EGX_download_report_{effective_day}.csv',csv2)
        st.download_button('⬇️ تحميل الشموع CSV',csv1,file_name=f'EGX_daily_candles_{effective_day}.csv',mime='text/csv',use_container_width=True)
        st.download_button('⬇️ تحميل تقرير الأسهم CSV',csv2,file_name=f'EGX_download_report_{effective_day}.csv',mime='text/csv',use_container_width=True)
        st.download_button('📦 تحميل الملفين ZIP',buf.getvalue(),file_name=f'EGX_candles_package_{effective_day}.zip',mime='application/zip',use_container_width=True)
    except Exception as e:
        st.error(f'فشل التحميل: {e}')

st.divider()
st.caption('تنبيه: بيانات DataCedar مصدر بيانات تاريخية منسوبة لمصادرها وليست تغذية رسمية موحدة للبورصة. عدم وجود شمعة لا يعني أن السهم لم يتداول؛ قد تعني أن المزود لا يملك رصدًا لذلك اليوم.')
