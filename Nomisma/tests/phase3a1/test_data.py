import unittest
import pandas as pd
from _bootstrap import master,calendar,facts
from ashare_data import *

class AdapterTests(unittest.TestCase):
    def raw(self):return pd.DataFrame({'日期':['2025-04-25'],'开盘':[10.],'最高':[12.],'最低':[9.],'收盘':[11.],'成交量':[123.],'成交额':[130000.]})
    def bars(self,raw=None,**kw):
        return normalize_bars(self.raw() if raw is None else raw,source='eastmoney_daily',master=master(),exchange='SZSE',code='000001',volume_unit=kw.get('volume_unit','unknown'),amount_unit=kw.get('amount_unit','unknown'))
    def test_identity_and_input_not_mutated(self):
        r=self.raw();before=r.copy(deep=True);out=self.bars(r);pd.testing.assert_frame_equal(r,before)
        self.assertEqual(out.security_id.iloc[0],'sample_ping_an')
    def test_units_are_explicit(self):
        r=self.bars(volume_unit='lots_100',amount_unit='CNY');self.assertEqual(r.volume_shares.iloc[0],12300)
        self.assertEqual(r.amount_cny.iloc[0],130000)
    def test_unknown_units_not_guessed(self):
        r=self.bars();self.assertTrue(r.volume_shares.isna().all());self.assertTrue(r.amount_cny.isna().all())
    def test_sina_mapping(self):
        r=self.raw().rename(columns={'日期':'date','开盘':'open','最高':'high','最低':'low','收盘':'close','成交量':'volume','成交额':'amount'})
        out=normalize_bars(r,source='sina_daily',master=master(),exchange='SZSE',code='000001',volume_unit='shares')
        self.assertEqual(out.volume_shares.iloc[0],123)
    def test_invalid_measurements(self):
        for field,value in [('开盘',0),('最高',8),('成交量',-1),('收盘',float('inf')),('开盘',True)]:
            r=self.raw().astype(object);r.loc[0,field]=value
            with self.subTest(field=field,value=value),self.assertRaises(DatasetError):self.bars(r)
    def test_duplicate_and_changed_schema(self):
        for r in [pd.concat([self.raw(),self.raw()]),self.raw().drop(columns='最高')]:
            with self.assertRaises(DatasetError):self.bars(r)
    def test_unknown_identity_and_cross_exchange(self):
        for exchange,code in [('SSE','000001'),('SZSE','123456')]:
            with self.assertRaises(DatasetError):normalize_bars(self.raw(),source='eastmoney_daily',master=master(),exchange=exchange,code=code,volume_unit='shares')
    def test_master_overlap_rejected(self):
        m=master();n=m.copy();n['valid_from']='2000-01-01';n['security_id']='another_security'
        with self.assertRaises(DatasetError):validate_master(pd.concat([m,n]))
    def test_code_reuse_nonoverlapping(self):
        m=master();m['valid_to']='2020-01-01';n=m.copy();n['valid_from']='2020-01-01';n['valid_to']=None;n['security_id']='new_security'
        m=pd.concat([m,n]);self.assertEqual(identity(m,'SZSE','000001','2020-01-01'),'new_security')
    def test_master_knowledge_time(self):
        with self.assertRaises(DatasetError):identity(master(),'SZSE','000001','2025-04-25',decision_at='1991-04-01T00:00:00Z')
    def test_calendar_timezone_and_duplicates(self):
        c=calendar();self.assertTrue(c.open_at.iloc[0].endswith('+00:00'))
        with self.assertRaises(DatasetError):validate_calendar(pd.concat([c,c]),'SZSE')
    def test_missing_bars_not_inferred_as_suspended(self):
        r=bar_coverage(self.bars(),calendar(),exchange='SZSE',start='2025-04-25',end='2025-04-30',security_id='sample_ping_an')
        self.assertEqual(len(r['missing_sessions']),3);self.assertEqual(r['status'],'fail')
        self.assertEqual(r['missing_reason'],'unknown_not_inferred_as_suspension')
    def test_calendar_dates_not_inferred_from_bars(self):
        raw=self.raw();raw.loc[0,'日期']='2025-04-26'
        r=bar_coverage(self.bars(raw),calendar(),exchange='SZSE',start='2025-04-25',end='2025-04-30',security_id='sample_ping_an')
        self.assertEqual(r['unexpected_sessions'],['2025-04-26'])
    def test_snapshot_rejects_future_suspensions(self):
        r=audit_suspension_snapshot(pd.DataFrame({'停牌时间':['2025-01-06','2026-11-03']}),requested_session='2025-01-06')
        self.assertEqual(r['future_start_rows'],1);self.assertEqual(r['historical_snapshot_status'],'rejected')
    def test_snapshot_no_false_certification(self):
        r=audit_suspension_snapshot(pd.DataFrame({'停牌时间':['2025-01-06']}),requested_session='2025-01-06')
        self.assertFalse(r['strict_history_certified']);self.assertEqual(r['historical_snapshot_status'],'unverified')
    def test_financial_snapshot_preserves_rejection(self):
        raw=pd.DataFrame({'报告日':['19911231','20241231'],'公告日期':['19911231','20250301'],
            '更新日期':['2020-03-13','2025-03-01'],'资产总计':[10,20]})
        out=normalize_sina_financials(raw,security_id='sample_ping_an',metrics=['资产总计'])
        self.assertEqual(out.iloc[0].rejection_reason,'DISCLOSURE_TIME_UNVERIFIED')
        self.assertEqual(set(out.evidence_status),{'unverified'})

class PITTests(unittest.TestCase):
    def query(self,f=None,at='2025-04-28T10:00:00+08:00',**kw):
        return financial_asof(facts() if f is None else f,pd.DataFrame([{'security_id':'sample_ping_an','report_period':'2024-12-31','metric':'assets'}]),calendar=calendar(),exchange='SZSE',decision_at=at,**kw)
    def test_no_lookahead_before_announcement(self):
        r=self.query(at='2025-04-25T10:00:00+08:00');self.assertTrue(r.value.isna().all());self.assertEqual(r.reason.iloc[0],'NOT_AVAILABLE_AT_DECISION')
    def test_date_only_waits_next_session_open(self):
        self.assertTrue(self.query(at='2025-04-28T09:29:59+08:00').value.isna().all())
        self.assertEqual(self.query(at='2025-04-28T09:30:00+08:00').value.iloc[0],100)
    def test_revision_selected_only_when_available(self):
        self.assertEqual(self.query(at='2025-04-29T20:00:00+08:00').value.iloc[0],100)
        self.assertEqual(self.query(at='2025-04-30T09:30:00+08:00').value.iloc[0],110)
    def test_revision_availability_can_delay(self):
        f=facts();f.loc[1,'revision_available_at']='2025-04-30T12:00:00+08:00'
        self.assertEqual(self.query(f,at='2025-04-30T10:00:00+08:00').value.iloc[0],100)
    def test_timestamp_disclosure(self):
        f=facts().iloc[:1].copy();f['precision']='timestamp';f['announced_at']='2025-04-25T18:00:00+08:00'
        self.assertEqual(self.query(f,at='2025-04-25T18:00:00+08:00').value.iloc[0],100)
    def test_unknown_evidence_not_approved(self):
        f=facts();f['evidence_status']='unverified';r=self.query(f)
        self.assertTrue(r.value.isna().all());self.assertEqual(r.reason.iloc[0],'DISCLOSURE_EVIDENCE_UNVERIFIED')
    def test_unreviewed_latest_does_not_fall_back(self):
        f=facts();f.loc[1,'evidence_status']='unverified'
        self.assertEqual(self.query(f,at='2025-04-30T10:00:00+08:00').reason.iloc[0],'DISCLOSURE_EVIDENCE_UNVERIFIED')
    def test_same_day_period_unverified_rejected(self):
        f=facts();f.loc[0,'announced_at']='2024-12-31';f['evidence_status']='unverified'
        with self.assertRaises(DatasetError):self.query(f)
    def test_revision_conflict(self):
        f=facts();f.loc[1,'announced_at']=f.loc[0,'announced_at'];f.loc[1,'revision_available_at']=f.loc[0,'revision_available_at']
        with self.assertRaises(DatasetError):self.query(f)
    def test_missing_query_returns_ledger_row(self):
        f=facts();f['metric']='other';r=self.query(f);self.assertEqual(len(r),1);self.assertEqual(r.reason.iloc[0],'FACT_MISSING')
    def test_naive_timestamp_rejected(self):
        with self.assertRaises(DatasetError):self.query(at='2025-04-28T10:00:00')
    def test_missing_calendar_not_silently_ignored(self):
        f=facts();f.loc[1,'announced_at']='2025-05-02'
        with self.assertRaises(DatasetError):self.query(f,at='2025-05-04T10:00:00Z')
    def test_calendar_start_not_used_as_arbitrary_disclosure_delay(self):
        f=facts();f.loc[0,'announced_at']='2025-04-24'
        with self.assertRaises(DatasetError):self.query(f)
    def test_nonfinite_financial_value_rejected(self):
        f=facts();f.loc[0,'value']=float('nan')
        with self.assertRaises(DatasetError):self.query(f)
    def test_gate_cannot_promote_sample_to_backtest(self):
        r=assess_gate(pit_result=self.query());self.assertFalse(r['strict_backtest_ready']);self.assertEqual(r['decision'],'research_only')

class StateTests(unittest.TestCase):
    def events(self):return pd.DataFrame([{'event_id':'synthetic_st','security_id':'s','state_type':'st','value':'true',
       'effective_from':'2025-04-25','effective_to':'2025-04-30','known_at':'2025-04-24T18:00:00+08:00','evidence_id':'synthetic'}])
    def query(self,events=None,session='2025-04-25',at='2025-04-25T00:00:00Z',kind='st'):
        return state_asof(self.events() if events is None else events,pd.DataFrame([{'security_id':'s','state_type':kind,'session':session}]),decision_at=at)
    def test_known_effective_state(self):self.assertEqual(self.query().value.iloc[0],'true')
    def test_future_knowledge_cannot_leak(self):self.assertEqual(self.query(at='2025-04-23T00:00:00Z').reason.iloc[0],'STATE_UNKNOWN')
    def test_half_open_end(self):self.assertEqual(self.query(session='2025-04-30').reason.iloc[0],'STATE_UNKNOWN')
    def test_missing_state_not_assumed_tradable(self):self.assertEqual(self.query(kind='price_limit').reason.iloc[0],'STATE_UNKNOWN')
    def test_conflict(self):
        e=self.events();n=e.copy();n['event_id']='different';n['value']='false'
        with self.assertRaises(DatasetError):self.query(pd.concat([e,n]))
    def test_all_six_categories(self):
        e=self.events();rows=[];qs=[]
        for kind in STATE_TYPES:
            row=e.iloc[0].to_dict();row.update(event_id='synthetic_'+kind,state_type=kind);rows.append(row)
            qs.append({'security_id':'s','state_type':kind,'session':'2025-04-25'})
        result=state_asof(pd.DataFrame(rows),pd.DataFrame(qs),decision_at='2025-04-25T00:00:00Z')
        self.assertEqual(len(result),6);self.assertTrue(result.reason.isna().all())

if __name__=='__main__':unittest.main()
