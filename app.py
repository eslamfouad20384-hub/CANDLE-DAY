import io, os, time, zipfile
from datetime import date, datetime, timedelta, timezone
import pandas as pd
import requests
import streamlit as st

BASE = 'https://api.datacedar.com/v3'
HEADERS_BASE = {'Accept': 'application/json', 'User-Agent': 'EGX-Candle-Downloader/2.0'}

st.set_page_config(page_title='EGX Daily Candle Downloader', page_icon='📈', layout='wide')
st.title('📈 EGX Daily Candle Downloader — Mapping Fix')
st.caption('إصدار إصلاح المطابقة: يبحث عن رمز التداول عبر DataCedar Search، ويستخدم sid الكامل بصيغة egx:EGS… لجلب الشموع.')

@st.cache_data(ttl=3600, show_spinner=False)
def load_symbols():
    with open('symbols.txt', encoding='utf-8') as f:
        return list(dict.fromkeys(x.strip().upper().replace('.CA', '') for x in f if x.strip()))

def headers(key):
    return {**HEADERS_BASE, 'Authorization': f'Bearer {key}', 'X-API-Key': key}

def parse_data_list(payload):
    data = payload.get('data', []) if isinstance(payload, dict) else []
    if isinstance(data, dict):
        for k in ('results', 'items', 'assets', 'symbols'):
            if isinstance(data.get(k), list):
                return data[k]
        return [v for v in data.values() if isinstance(v, dict)]
    return data if isinstance(data, list) else []

@st.cache_data(ttl=3600, show_spinner=False)
def get_registry(_key):
    rows, cursor, pages = [], None, 0
    while pages < 20:
        params = {'exchange': 'egx', 'limit': 1000}
        if cursor:
            params['cursor'] = cursor
        r = requests.get(f'{BASE}/stocks/assets', headers=headers(_key), params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f'فشل سجل الأصول HTTP {r.status_code}: {r.text[:300]}')
        j = r.json()
        rows.extend(x for x in parse_data_list(j) if isinstance(x, dict))
        pg = j.get('pagination') or {}
        cursor = pg.get('next_cursor') if isinstance(pg, dict) else None
        pages += 1
        if not cursor:
            break
    by_sid = {}
    for a in rows:
        sid = a.get('sid') or a.get('exchange_code')
        if sid:
            by_sid[str(sid).lower()] = a
    return rows, by_sid

def resolve_ticker(key, ticker, registry_by_sid):
    """Resolve local EGX ticker via provider search; never fuzzy-match company names."""
    r = requests.get(f'{BASE}/search', headers=headers(key),
                     params={'q': ticker, 'exchange': 'egx', 'limit': 20}, timeout=30)
    if r.status_code != 200:
        return None, f'SEARCH HTTP {r.status_code}: {r.text[:180]}', []
    payload = r.json()
    candidates = []
    for x in parse_data_list(payload):
        if not isinstance(x, dict):
            continue
        sid = x.get('sid') or x.get('exchange_code')
        exch = str(x.get('exchange') or '').lower()
        if sid and (not exch or exch == 'egx'):
            row = dict(registry_by_sid.get(str(sid).lower(), {}))
            row.update(x)
            row['sid'] = str(sid)
            candidates.append(row)
    # Accept only a single unique EGX result. Multiple results require human confirmation.
    unique = {str(x['sid']).lower(): x for x in candidates}
    candidates = list(unique.values())
    if len(candidates) == 1:
        return candidates[0], 'تم العثور على نتيجة EGX واحدة من بحث DataCedar', candidates
    if len(candidates) == 0:
        return None, 'لم يرجع بحث DataCedar نتيجة لهذا الرمز', []
    # Prefer an exact provider ticker match only if unique.
    exact = [x for x in candidates if str(x.get('symbol') or '').upper().replace('.CA','') == ticker.upper()]
    if len(exact) == 1:
        return exact[0], 'تطابق رمز حرفي من بحث DataCedar', candidates
    return None, f'نتائج متعددة ({len(candidates)}) — محتاجة تأكيد يدوي', candidates

def fetch_batch(key, sids, start_day, end_day):
    params = {'symbols': ','.join(sids), 'timeframe': '1d', 'from': start_day.isoformat(),
              'to': end_day.isoformat(), 'adjustment': 'raw', 'order': 'asc', 'limit': 50000}
    r = requests.get(f'{BASE}/stocks/bars', headers=headers(key), params=params, timeout=60)
    if r.status_code != 200:
        try:
            err = r.json().get('error', {})
            msg = err.get('message') or err.get('code') or r.text[:250]
        except Exception:
            msg = r.text[:250]
        raise RuntimeError(f'BARS HTTP {r.status_code}: {msg}')
    j = r.json()
    data = j.get('data', {})
    return data if isinstance(data, dict) else {}, j

def extract_daily(series, target_day, latest):
    valid = []
    for item in (series or []):
        ts = item.get('ts_ms')
        if ts is None:
            continue
        try:
            d = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).date()
        except Exception:
            continue
        if d <= target_day and (latest or d == target_day):
            if all(item.get(k) is not None for k in ('open','high','low','close','volume')):
                valid.append((d, item))
    return max(valid, key=lambda z: z[0]) if valid else (None, None)

with st.sidebar:
    st.header('إعدادات التحميل')
    try:
        secret_key = str(st.secrets.get('DATACEDAR_API_KEY', '') or st.secrets.get('DATACEDAR_KEY', '')).strip()
    except Exception:
        secret_key = ''
    ui_key = st.text_input('DataCedar API Key (اختياري لو موجود في Secrets)', type='password', value='')
    api_key = ui_key.strip() or secret_key
    if secret_key and not ui_key.strip():
        st.caption('تم العثور على المفتاح في Streamlit Secrets.')
    selected_date = st.date_input('تاريخ جلسة التداول', value=date.today()-timedelta(days=1), max_value=date.today())
    use_latest = st.checkbox('آخر جلسة متاحة حتى التاريخ المحدد', value=True)
    delay = st.number_input('الفاصل بين طلبات البحث (ثانية)', min_value=1.0, max_value=10.0, value=1.1, step=0.1)
    st.caption('حد Explorer المجاني: 3 رموز في طلب الشموع وطلب API واحد/ثانية.')

symbols = load_symbols()
st.metric('عدد الأسهم في القائمة', len(symbols))
st.info('يجب وجود symbols.txt بجوار app.py في GitHub. رموز التصدير ستظهر بلاحقة .CA.')
st.warning('المطابقة تتم عبر /search ولا تستخدم تشابه أسماء الشركات. النتائج المتعددة أو غير الموجودة لا يتم تخمينها.')

if st.button('🔎 ابدأ المطابقة وجلب الشموع', type='primary', use_container_width=True):
    if not api_key:
        st.error('أضف DATACEDAR_API_KEY في Streamlit Secrets أو المفتاح في الشريط الجانبي.')
        st.stop()
    try:
        with st.spinner('تحميل سجل EGX من DataCedar...'):
            registry_rows, registry_by_sid = get_registry(api_key)
        st.success(f'تم تحميل سجل المزود: {len(registry_rows)} أصل.')
        reg_df = pd.DataFrame(registry_rows)
        if not reg_df.empty:
            cols = [c for c in ['symbol','exchange_code','exchange','name','sid','security_id','bars_url','status','asset_class'] if c in reg_df.columns]
            st.download_button('⬇️ تنزيل سجل DataCedar الكامل CSV', reg_df[cols].to_csv(index=False).encode('utf-8-sig'),
                               file_name='DataCedar_EGX_registry.csv', mime='text/csv')

        resolved, unresolved, ambiguous = {}, [], []
        prog = st.progress(0)
        status = st.empty()
        for i, ticker in enumerate(symbols):
            status.write(f'بحث الرمز {i+1}/{len(symbols)}: {ticker}')
            try:
                row, message, candidates = resolve_ticker(api_key, ticker, registry_by_sid)
                if row:
                    resolved[ticker] = row
                elif candidates:
                    ambiguous.append({'ticker':ticker, 'message':message,
                                      'candidates':'; '.join(f"{x.get('sid')} | {x.get('name','')}" for x in candidates)})
                else:
                    unresolved.append({'ticker':ticker, 'message':message})
            except Exception as e:
                unresolved.append({'ticker':ticker, 'message':str(e)[:250]})
            prog.progress((i+1)/len(symbols))
            if i < len(symbols)-1:
                time.sleep(float(delay))
        status.empty()
        mapping_rows = []
        for ticker, row in resolved.items():
            mapping_rows.append({'ticker':ticker, 'sid':row.get('sid') or row.get('exchange_code'),
                                 'provider_symbol':row.get('symbol',''), 'name':row.get('name',''),
                                 'security_id':row.get('security_id',''), 'bars_url':row.get('bars_url',''), 'match':'بحث DataCedar'})
        mapping_df = pd.DataFrame(mapping_rows, columns=['ticker','sid','provider_symbol','name','security_id','bars_url','match'])
        st.subheader('نتيجة المطابقة')
        a,b,c = st.columns(3); a.metric('مطابقات مؤكدة',len(resolved)); b.metric('غير موجودة',len(unresolved)); c.metric('تحتاج تأكيد',len(ambiguous))
        if not mapping_df.empty:
            st.dataframe(mapping_df, use_container_width=True, hide_index=True)
            st.download_button('⬇️ تنزيل جدول الربط CSV',mapping_df.to_csv(index=False).encode('utf-8-sig'),
                               file_name='EGX_DataCedar_Mapping.csv',mime='text/csv')
        if ambiguous:
            st.subheader('نتائج ملتبسة — لم يتم اختيار أي نتيجة تلقائيًا')
            st.dataframe(pd.DataFrame(ambiguous),use_container_width=True,hide_index=True)
        if unresolved:
            st.subheader('رموز لم يعثر عليها البحث')
            st.dataframe(pd.DataFrame(unresolved),use_container_width=True,hide_index=True)
        if not resolved:
            st.error('لم يتم تأكيد أي رمز. نزّل سجل DataCedar وملف تقرير المطابقة وأرسلهم للمراجعة؛ لم يتم إرسال طلبات شموع خاطئة.')
            st.stop()

        st.subheader('تحميل الشموع اليومية')
        from_day = selected_date - timedelta(days=14) if use_latest else selected_date
        targets = list(resolved.items())
        batches = [targets[i:i+3] for i in range(0,len(targets),3)]
        candle_rows, report_rows = [], []
        bar = st.progress(0); msg = st.empty()
        for bi, batch in enumerate(batches):
            sids = [str(row.get('sid') or row.get('exchange_code')) for _,row in batch]
            msg.write(f'تحميل المجموعة {bi+1}/{len(batches)}: '+', '.join(t for t,_ in batch))
            try:
                data, payload = fetch_batch(api_key,sids,from_day,selected_date)
                for ticker,row in batch:
                    sid = str(row.get('sid') or row.get('exchange_code'))
                    series = data.get(sid, data.get(sid.lower(), []))
                    actual_day,item = extract_daily(series,selected_date,use_latest)
                    if item:
                        candle_rows.append({'symbol':ticker+'.CA','sid':sid,'date':actual_day.isoformat(),
                            'open':float(item['open']),'high':float(item['high']),'low':float(item['low']),
                            'close':float(item['close']),'volume':float(item['volume']),
                            'adjustment':item.get('adjustment','raw'),'source':'DataCedar'})
                        report_rows.append({'symbol':ticker+'.CA','sid':sid,'status':'نجاح','message':f'شمعة OHLCV بتاريخ {actual_day}'})
                    else:
                        report_rows.append({'symbol':ticker+'.CA','sid':sid,'status':'لا توجد شمعة','message':'لم يرجع المزود شمعة مكتملة في النطاق'})
            except Exception as e:
                for ticker,row in batch:
                    report_rows.append({'symbol':ticker+'.CA','sid':row.get('sid',''),'status':'خطأ','message':str(e)[:300]})
            bar.progress((bi+1)/len(batches))
            if bi < len(batches)-1:
                time.sleep(float(delay))
        for x in unresolved:
            report_rows.append({'symbol':x['ticker']+'.CA','sid':'','status':'غير مطابق','message':x['message']})
        for x in ambiguous:
            report_rows.append({'symbol':x['ticker']+'.CA','sid':'','status':'ملتبس','message':x['message']})
        candles = pd.DataFrame(candle_rows,columns=['symbol','sid','date','open','high','low','close','volume','adjustment','source'])
        report = pd.DataFrame(report_rows)
        st.success(f'اكتمل التحميل: {len(candles)} شمعة مكتملة من أصل {len(symbols)} سهم.')
        x1,x2,x3=st.columns(3); x1.metric('شموع مكتملة',len(candles)); x2.metric('مشكلات المطابقة/البيانات',len(report)-len(candles)); x3.metric('التغطية',f'{len(candles)/len(symbols)*100:.1f}%')
        st.dataframe(candles,use_container_width=True,hide_index=True)
        st.subheader('تقرير كل الأسهم')
        st.dataframe(report,use_container_width=True,hide_index=True)
        csv1=candles.to_csv(index=False).encode('utf-8-sig'); csv2=report.to_csv(index=False).encode('utf-8-sig')
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr(f'EGX_daily_candles_{selected_date}.csv',csv1)
            z.writestr(f'EGX_download_report_{selected_date}.csv',csv2)
            z.writestr('EGX_DataCedar_Mapping.csv',mapping_df.to_csv(index=False).encode('utf-8-sig'))
        st.download_button('⬇️ تحميل الشموع CSV',csv1,file_name=f'EGX_daily_candles_{selected_date}.csv',mime='text/csv')
        st.download_button('⬇️ تحميل تقرير الأخطاء CSV',csv2,file_name=f'EGX_download_report_{selected_date}.csv',mime='text/csv')
        st.download_button('📦 تحميل الحزمة ZIP',buf.getvalue(),file_name=f'EGX_DataCedar_Package_{selected_date}.zip',mime='application/zip')
    except Exception as e:
        st.error(f'فشل التنفيذ: {e}')

st.divider()
st.caption('ملاحظة: البحث يتم لكل رمز على حدة وفق حد الخطة. لم يتم اختبار اتصال API بمفتاحك من هنا؛ نجاح البحث وجلب الشموع يتأكد عند تشغيل التطبيق على Streamlit.')
