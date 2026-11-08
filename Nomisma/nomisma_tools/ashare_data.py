"""Phase 3A-1 finite data adapters and point-in-time checks; no MCP registration.

Security identities are supplied by an explicit master, never guessed from code.
Dates and source declarations are not certifications of historical completeness.
"""
from datetime import date
import math,re
import numpy as np
import pandas as pd
from dataset_store import DatasetError,check_shape

VERSION='ashare-data-1'
EXCHANGES=('SSE','SZSE','BSE')

def fail(code,message):raise DatasetError(code,message)

def columns(frame,names,keys=()):
    if not isinstance(frame,pd.DataFrame):fail('INVALID_ARGUMENT','A table is required')
    check_shape(frame)
    if not frame.columns.is_unique or not set(names)<=set(frame.columns):
        fail('SCHEMA_MISMATCH','Required unique columns are missing')
    if frame[list(names)].isna().any().any():fail('MISSING_REQUIRED','Required values are missing')
    if keys and frame.duplicated(list(keys)).any():fail('DUPLICATE_KEY','Table keys must be unique')

def day(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        fail('INVALID_DATE','Expected an ISO calendar date')
    try:parsed=date.fromisoformat(value)
    except ValueError:fail('INVALID_DATE','Invalid calendar date')
    if parsed.year<1990:fail('INVALID_DATE','Placeholder or unsupported historical date')
    return parsed.isoformat()

def timestamp(value):
    try:
        if not isinstance(value,str):raise ValueError()
        t=pd.Timestamp(value)
        if pd.isna(t) or t.tzinfo is None or t.year<1990:raise ValueError()
        return t.tz_convert('UTC')
    except (TypeError,ValueError,OverflowError):fail('INVALID_TIMESTAMP','An explicit timezone-aware timestamp is required')

def number(value,*,nonnegative=False,positive=False):
    if isinstance(value,(bool,np.bool_)):fail('INVALID_NUMBER','Boolean is not a measurement')
    try:v=float(value)
    except (TypeError,ValueError,OverflowError):fail('INVALID_NUMBER','Expected a finite numeric value')
    if not math.isfinite(v) or nonnegative and v<0 or positive and v<=0:
        fail('INVALID_NUMBER','Measurement is nonfinite or outside its allowed range')
    return v

def validate_master(frame):
    required=['security_id','exchange','code','valid_from','known_at','evidence_id']
    columns(frame,required,('security_id','valid_from'))
    if 'valid_to' not in frame:fail('SCHEMA_MISMATCH','Master requires nullable valid_to')
    records=[]
    for r in frame.to_dict('records'):
        if (not isinstance(r['security_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',r['security_id'])
            or r['exchange'] not in EXCHANGES or not isinstance(r['code'],str) or not re.fullmatch(r'\d{6}',r['code'])):
            fail('SECURITY_ID_INVALID','Security identity or explicit exchange/code is invalid')
        start=day(r['valid_from']);end=None if pd.isna(r['valid_to']) else day(r['valid_to'])
        if end is not None and end<=start:fail('INVALID_INTERVAL','Intervals are half-open and must have positive length')
        timestamp(r['known_at']);records.append({**r,'valid_from':start,'valid_to':end})
    result=pd.DataFrame(records)
    for keys in [('exchange','code'),('security_id',)]:
        for _,g in result.groupby(list(keys)):
            g=g.sort_values('valid_from');previous=None
            for r in g.to_dict('records'):
                if previous is not None and (previous['valid_to'] is None or r['valid_from']<previous['valid_to']):
                    fail('IDENTITY_CONFLICT','Security master intervals overlap')
                previous=r
    return result

def identity(master,exchange,code,session,*,decision_at=None):
    m=validate_master(master);session=day(session)
    found=m[(m.exchange==exchange)&(m.code==code)&(m.valid_from<=session)&(m.valid_to.isna()|(m.valid_to>session))]
    if decision_at is not None:
        found=found[found.known_at.map(timestamp)<=timestamp(decision_at)]
    if len(found)!=1:fail('SECURITY_UNRESOLVED','No unique time-valid security mapping is known')
    return str(found.iloc[0].security_id)

def normalize_calendar(raw,*,exchange,evidence_id):
    if exchange not in EXCHANGES:fail('INVALID_ARGUMENT','Exchange must be explicit')
    columns(raw,['trade_date'],('trade_date',))
    dates=[day(str(v)[:10]) for v in raw.trade_date]
    result=pd.DataFrame({'exchange':exchange,'session':sorted(dates)})
    result['open_at']=[pd.Timestamp(d+'T09:30:00',tz='Asia/Shanghai').tz_convert('UTC').isoformat() for d in result.session]
    result['close_at']=[pd.Timestamp(d+'T15:00:00',tz='Asia/Shanghai').tz_convert('UTC').isoformat() for d in result.session]
    result['evidence_id']=evidence_id
    result['coverage_status']='candidate_not_exchange_certified'
    return result

def validate_calendar(calendar,exchange):
    columns(calendar,['exchange','session','open_at','close_at'],('exchange','session'))
    c=calendar[calendar.exchange==exchange].sort_values('session')
    if c.empty:fail('CALENDAR_UNAVAILABLE','Exchange calendar is unavailable')
    for r in c.to_dict('records'):
        day(r['session']);op=timestamp(r['open_at']);cl=timestamp(r['close_at'])
        if op>=cl or op.tz_convert('Asia/Shanghai').date().isoformat()!=r['session'] or cl.tz_convert('Asia/Shanghai').date().isoformat()!=r['session']:
            fail('CALENDAR_INVALID','Calendar times do not match their session')
    return c

def normalize_bars(raw,*,source,master,exchange,code,volume_unit,amount_unit='unknown'):
    mappings={
      'eastmoney_daily':{'日期':'session','开盘':'open','最高':'high','最低':'low','收盘':'close','成交量':'volume','成交额':'amount'},
      'sina_daily':{'date':'session','open':'open','high':'high','low':'low','close':'close','volume':'volume','amount':'amount'}}
    if source not in mappings or volume_unit not in ('shares','lots_100','unknown') or amount_unit not in ('CNY','unknown'):
        fail('INVALID_ARGUMENT','Only finite source and explicit unit policies are supported')
    mapping=mappings[source];base={k:v for k,v in mapping.items() if v!='amount'}
    columns(raw,list(base));out=raw[list(base)].rename(columns=base).copy()
    out['session']=[day(str(v)[:10]) for v in out.session]
    if out.session.duplicated().any():fail('DUPLICATE_KEY','Bars contain duplicate sessions')
    for c in ('open','high','low','close'):out[c]=out[c].map(lambda v:number(v,positive=True))
    out['volume_raw']=out.pop('volume').map(lambda v:number(v,nonnegative=True))
    if ((out.high<out[['open','close','low']].max(axis=1))|(out.low>out[['open','close','high']].min(axis=1))).any():
        fail('INVALID_OHLC','OHLC price bounds are inconsistent')
    out['volume_shares']=out.volume_raw*(100 if volume_unit=='lots_100' else 1) if volume_unit!='unknown' else None
    out['volume_unit_input']=volume_unit
    amount_key=next(k for k,v in mapping.items() if v=='amount')
    out['amount_raw']=raw[amount_key].map(lambda v:number(v,nonnegative=True)) if amount_key in raw else None
    out['amount_cny']=out.amount_raw if amount_unit=='CNY' else None
    out['amount_unit_input']=amount_unit;out['price_basis']='unadjusted';out['currency']='CNY'
    out['security_id']=[identity(master,exchange,code,d) for d in out.session]
    out['exchange']=exchange;out['source']=source
    out['unit_status']='declared_not_independently_certified'
    return out.sort_values('session').reset_index(drop=True)

def bar_coverage(bars,calendar,*,exchange,start,end,security_id):
    start=day(start);end=day(end)
    if start>end:fail('INVALID_INTERVAL','Coverage dates are reversed')
    c=validate_calendar(calendar,exchange);expected=c[(c.session>=start)&(c.session<=end)]
    if expected.empty:fail('CALENDAR_UNAVAILABLE','No sessions in the requested interval')
    columns(bars,['security_id','session','exchange'],('security_id','session'))
    selected=bars[(bars.security_id==security_id)&(bars.exchange==exchange)]
    dates=set(selected.session);wanted=set(expected.session)
    missing=sorted(wanted-dates);unexpected=sorted(dates-set(c.session))
    return {'expected_sessions':len(wanted),'observed_in_range':len(dates&wanted),
            'missing_sessions':missing,'unexpected_sessions':unexpected,
            'status':'pass' if not missing and not unexpected else 'fail',
            'missing_reason':'unknown_not_inferred_as_suspension' if missing else None}

def validate_financials(facts):
    names=['security_id','report_period','metric','value','unit','currency','announced_at','precision',
           'revision_id','revision_available_at','evidence_id','evidence_status']
    columns(facts,names,('security_id','report_period','metric','revision_id'))
    if not set(facts.precision)<= {'date','timestamp'} or not set(facts.evidence_status)<= {'unverified','reviewed_original'}:
        fail('INVALID_ARGUMENT','Unsupported financial precision or evidence status')
    out=facts.copy()
    for r in out.to_dict('records'):
        day(r['report_period']);number(r['value'])
        announced=day(r['announced_at']) if r['precision']=='date' else timestamp(r['announced_at']).tz_convert('Asia/Shanghai').date().isoformat()
        # A report period is not its own disclosure date. Same-day values need original verification.
        if announced<r['report_period'] or announced==r['report_period'] and r['evidence_status']!='reviewed_original':
            fail('DISCLOSURE_TIME_UNVERIFIED','Disclosure date is before or suspiciously equal to the report period')
        timestamp(r['revision_available_at'])
    return out

def financial_asof(facts,queries,*,calendar,exchange,decision_at,require_reviewed=True):
    f=validate_financials(facts);c=validate_calendar(calendar,exchange);decision=timestamp(decision_at)
    columns(queries,['security_id','report_period','metric'],('security_id','report_period','metric'))
    effective=[]
    for r in f.to_dict('records'):
        if r['precision']=='date':
            if day(r['announced_at'])<c.session.iloc[0]:
                fail('CALENDAR_UNAVAILABLE','Calendar does not cover the disclosure date')
            future=c[c.session>day(r['announced_at'])]
            public=timestamp(future.iloc[0].open_at) if len(future) else None
            if public is None and day(r['announced_at'])<=decision.tz_convert('Asia/Shanghai').date().isoformat():
                fail('CALENDAR_UNAVAILABLE','No calendar session after an already disclosed fact')
        else:public=timestamp(r['announced_at'])
        effective.append(max(public,timestamp(r['revision_available_at'])) if public is not None else None)
    f['_effective']=effective
    if f.dropna(subset=['_effective']).duplicated(['security_id','report_period','metric','_effective']).any():
        fail('REVISION_CONFLICT','Multiple financial versions share an availability timestamp')
    rows=[]
    for q in queries.to_dict('records'):
        day(q['report_period'])
        group=f[(f.security_id==q['security_id'])&(f.report_period==q['report_period'])&(f.metric==q['metric'])]
        known=group[group._effective.map(lambda x:x is not None and not pd.isna(x) and x<=decision)]
        # Pick latest known revision before checking evidence; do not silently fall back to stale reviewed data.
        chosen=known.sort_values('_effective').iloc[-1] if len(known) else None
        reason='NOT_AVAILABLE_AT_DECISION' if len(group) and chosen is None else 'FACT_MISSING' if group.empty else None
        if chosen is not None and require_reviewed and chosen.evidence_status!='reviewed_original':reason='DISCLOSURE_EVIDENCE_UNVERIFIED'
        rows.append({**q,'decision_at':decision.isoformat(),'value':None if reason else number(chosen.value),
            'unit':None if chosen is None else chosen.unit,'currency':None if chosen is None else chosen.currency,
            'revision_id':None if chosen is None else chosen.revision_id,
            'available_at':None if chosen is None else chosen['_effective'].isoformat(),
            'evidence_id':None if chosen is None else chosen.evidence_id,'reason':reason,
            'evidence_status':None if chosen is None else chosen.evidence_status})
    return pd.DataFrame(rows)

def normalize_sina_financials(raw,*,security_id,metrics):
    """Latest website snapshot only. Never promote it to reviewed PIT history."""
    if not isinstance(metrics,list) or not 1<=len(metrics)<=10 or len(set(metrics))!=len(metrics):
        fail('INVALID_ARGUMENT','Choose one to ten explicit financial fields')
    if not set(['报告日','公告日期','更新日期',*metrics])<=set(raw.columns):fail('SCHEMA_MISMATCH','Financial source fields changed')
    if not raw.columns.is_unique or len(raw)>1000:fail('RESOURCE_LIMIT','Financial raw shape is not supported')
    rows=[]
    for r in raw.to_dict('records'):
        for metric in metrics:
            period=str(r['报告日']);period=period[:4]+'-'+period[4:6]+'-'+period[6:] if re.fullmatch(r'\d{8}',period) else period
            announced=str(r['公告日期']);announced=announced[:4]+'-'+announced[4:6]+'-'+announced[6:] if re.fullmatch(r'\d{8}',announced) else announced
            reason=None
            try:
                day(period);day(announced)
                if announced<=period:reason='DISCLOSURE_TIME_UNVERIFIED'
                value=None if pd.isna(r[metric]) else number(r[metric])
                if value is None:reason=reason or 'VALUE_MISSING'
            except DatasetError:reason='SOURCE_VALUE_OR_DATE_INVALID';value=None
            rows.append({'security_id':security_id,'report_period':period,'metric':metric,'value':value,
                'announced_at':announced,'source_updated_at':str(r['更新日期']),
                'evidence_status':'unverified','pit_status':'latest_snapshot_not_revision_history',
                'rejection_reason':reason})
    result=pd.DataFrame(rows);check_shape(result);return result

STATE_TYPES=('listing','delisting','st','suspension','corporate_action','price_limit')
def state_asof(events,queries,*,decision_at):
    names=['event_id','security_id','state_type','value','effective_from','effective_to','known_at','evidence_id']
    columns(events,[n for n in names if n!='effective_to'],('event_id',))
    if 'effective_to' not in events:fail('SCHEMA_MISMATCH','State events require nullable effective_to')
    columns(queries,['security_id','state_type','session'],('security_id','state_type','session'))
    if not set(events.state_type)<=set(STATE_TYPES) or not set(queries.state_type)<=set(STATE_TYPES):
        fail('INVALID_ARGUMENT','Unsupported state category')
    decision=timestamp(decision_at);out=[]
    for r in events.to_dict('records'):
        start=day(r['effective_from']);timestamp(r['known_at'])
        if not pd.isna(r['effective_to']) and day(r['effective_to'])<=start:fail('INVALID_INTERVAL','State interval is reversed')
    for q in queries.to_dict('records'):
        session=day(q['session'])
        g=events[(events.security_id==q['security_id'])&(events.state_type==q['state_type'])]
        g=g[g.known_at.map(timestamp)<=decision]
        g=g[(g.effective_from<=session)&(g.effective_to.isna()|(g.effective_to>session))]
        if len(g)>1:fail('STATE_CONFLICT','Overlapping known states require explicit resolution')
        out.append({**q,'decision_at':decision.isoformat(),'event_id':None if g.empty else g.iloc[0].event_id,
                    'known_at':None if g.empty else g.iloc[0].known_at,'value':None if g.empty else g.iloc[0].value,
                    'evidence_id':None if g.empty else g.iloc[0].evidence_id,
                    'reason':'STATE_UNKNOWN' if g.empty else None})
    return pd.DataFrame(out)

def assess_gate(*,bars_report=None,pit_result=None,state_result=None):
    checks=[]
    checks.append({'id':'bar_coverage','status':bars_report['status'] if bars_report else 'unknown'})
    checks.append({'id':'financial_pit','status':('pass' if pit_result.reason.isna().all() and (pit_result.evidence_status=='reviewed_original').all() else 'fail')
                   if pit_result is not None and len(pit_result) else 'unknown'})
    for kind in STATE_TYPES:
        g=state_result[state_result.state_type==kind] if state_result is not None else None
        checks.append({'id':'state_'+kind,'status':('pass' if g.reason.isna().all() else 'fail') if g is not None and len(g) else 'unknown'})
    # These capabilities are outside A1: a few reviewed samples cannot certify the universe or trading engine.
    for name in ('historical_universe_complete','corporate_action_cash_accounting','historical_rule_versions','independent_evidence_audit'):
        checks.append({'id':name,'status':'unknown'})
    return {'schema_version':'ashare-gate-1','scope':'sample_data_validation_only','conditions':checks,
        'decision':'blocked' if any(c['status']=='fail' for c in checks) else 'research_only',
        'strict_backtest_ready':False,'limitations':['Candidate coverage and reviewer declarations are not full historical certification',
          'No trading engine, factor construction, or automatic strategy changes in Phase 3A-1']}

def audit_suspension_snapshot(raw,*,requested_session):
    """Detect the A0 counterexample without reinterpreting current rows as history."""
    requested=day(requested_session);columns(raw,['停牌时间'])
    parsed=pd.to_datetime(raw['停牌时间'],errors='coerce')
    bad=int(parsed.isna().sum());future=int((parsed.dt.strftime('%Y-%m-%d')>requested).sum())
    return {'requested_session':requested,'rows':len(raw),'unparseable_start_dates':bad,
        'future_start_rows':future,'historical_snapshot_status':'rejected' if bad or future else 'unverified',
        'reason':'RETURNED_ROWS_DO_NOT_PROVE_ASOF_SNAPSHOT',
        'strict_history_certified':False}
