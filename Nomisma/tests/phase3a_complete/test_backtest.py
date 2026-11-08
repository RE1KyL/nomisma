import unittest
import pandas as pd
from _bootstrap import market
from dataset_store import DatasetError
from quant_backtest import simulate
from quant_validation import performance

class BacktestTests(unittest.TestCase):
    def fixture(self):
        d,c,b,s,f,a,_,_=market(days=4,assets=('a',));b['open']=10.;b['close']=10.
        target=pd.DataFrame([{'signal_session':d[0],'execution_session':d[1],'formation_at':d[0]+'T15:05:00+08:00','security_id':'a','target_weight':1.}])
        return d,c,b,s,f,a,target
    def run_case(self,modify=None,**options):
        data=list(self.fixture())
        if modify:modify(data)
        d,c,b,s,f,a,t=data
        return simulate(b,t,s,c,f,a,initial_cash=10000.,slippage_bps=0.,stale_price_policy=options.get('stale','reject'),execution_mode=options.get('mode','declared_scenario'))
    def test_order_position_cash_nav_reconcile(self):
        r=self.run_case();self.assertEqual(r['fills'].quantity.iloc[0],1000);self.assertEqual(r['nav'].equity.tolist(),[10000.]*4)
        self.assertTrue(((r['nav'].cash+r['nav'].market_value+r['nav'].receivables-r['nav'].equity).abs()<1e-10).all())
    def test_fees_are_real_cash_and_no_borrowing(self):
        def change(x):x[4]['commission_rate']=.001;x[4]['minimum_commission']=5.
        r=self.run_case(change);self.assertEqual(r['fills'].quantity.iloc[0],900);self.assertGreater(r['summary']['total_fees'],0);self.assertTrue((r['nav'].cash>=0).all())
    def test_capacity_is_preopen_and_partial_fill_logged(self):
        def change(x):x[3]['capacity_shares']=200
        r=self.run_case(change);self.assertEqual(r['fills'].quantity.iloc[0],200);self.assertTrue((r['rejections'].reason=='PARTIAL_FILL_REMAINDER_NOT_RETRIED').all())
    def test_future_capacity_rejected(self):
        def change(x):x[3]['capacity_available_at']=x[3].session+'T16:00:00+08:00'
        with self.assertRaises(DatasetError):self.run_case(change)
    def test_same_session_execution_rejected(self):
        def change(x):x[6]['execution_session']=x[0][0]
        with self.assertRaises(DatasetError):self.run_case(change)
    def test_missing_state_not_tradable(self):
        def change(x):x[3]=x[3].iloc[1:]
        with self.assertRaises(DatasetError):self.run_case(change)
    def test_suspended_order_keeps_cash(self):
        def change(x):x[3].loc[x[3].session==x[0][1],'suspended']=True
        r=self.run_case(change);self.assertTrue(r['fills'].empty);self.assertEqual(r['nav'].equity.iloc[-1],10000.)
    def test_up_limit_rejected_conservatively(self):
        def change(x):x[3]['limit_policy']='bounded';x[3]['limit_up']=10.;x[3]['limit_down']=9.
        r=self.run_case(change);self.assertTrue(r['fills'].empty);self.assertEqual(r['rejections'].reason.iloc[0],'UP_LIMIT_CONSERVATIVE_REJECTION')
    def test_split_not_double_counted(self):
        def change(x):
            d=x[0];x[5]=pd.DataFrame([{'event_id':'split','security_id':'a','kind':'split','session':d[2],
                'known_at':d[1]+'T18:00:00+08:00','evidence_id':'synthetic','ratio':2.,'sellable_session':d[2]}])
            x[2].loc[x[2].session>=d[2],['open','close']]=5.
        r=self.run_case(change);self.assertEqual(r['positions'].quantity.iloc[-1],2000);self.assertEqual(r['nav'].equity.iloc[-1],10000.)
    def test_dividend_receivable_then_payment(self):
        def change(x):
            d=x[0];x[5]=pd.DataFrame([{'event_id':'dividend','security_id':'a','kind':'cash_dividend','session':d[2],
                'known_at':d[1]+'T18:00:00+08:00','evidence_id':'synthetic','record_session':d[1],'pay_session':d[3],
                'cash_per_share':1.,'tax_rate':0.}]);x[2].loc[x[2].session>=d[2],['open','close']]=9.
        r=self.run_case(change);self.assertEqual(r['nav'].receivables.iloc[2],1000.);self.assertEqual(r['nav'].cash.iloc[3],1000.)
        self.assertEqual(r['nav'].equity.iloc[-1],10000.)
    def test_delisting_needs_settlement_and_removes_position(self):
        def change(x):
            d=x[0];x[5]=pd.DataFrame([{'event_id':'delist','security_id':'a','kind':'delisting','session':d[2],
                'known_at':d[1]+'T18:00:00+08:00','evidence_id':'synthetic','settlement_price':5.}])
            x[3].loc[x[3].session>=d[2],'listed']=False;x[2]=x[2][x[2].session<d[2]]
        r=self.run_case(change);self.assertEqual(r['nav'].equity.iloc[-1],5000.);self.assertEqual(r['nav'].market_value.iloc[-1],0.)
    def test_unknown_delisting_does_not_silently_write_zero(self):
        def change(x):x[3].loc[x[3].session>=x[0][2],'listed']=False
        with self.assertRaises(DatasetError):self.run_case(change)
    def test_suspension_carry_is_explicit(self):
        def change(x):
            d=x[0][2];x[3].loc[x[3].session==d,'suspended']=True;x[2]=x[2][x[2].session!=d]
        with self.assertRaises(DatasetError):self.run_case(change)
        r=self.run_case(change,stale='carry_suspended');self.assertTrue(r['positions'].stale_valuation.any())
    def test_strict_mode_does_not_accept_synthetic_or_candidate_data(self):
        with self.assertRaises(DatasetError):self.run_case(mode='strict')
    def test_cancellation_returns_no_completed_result(self):
        d,c,b,s,f,a,t=self.fixture()
        with self.assertRaises(DatasetError):simulate(b,t,s,c,f,a,initial_cash=10000,slippage_bps=0,stale_price_policy='reject',execution_mode='declared_scenario',cancelled=lambda:True)
    def test_performance_reuses_real_wealth_math(self):
        r=self.run_case();p=performance(r['nav'],initial_equity=10000,periods_per_year=252,risk_free_annual=0.,fills=r['fills'])
        self.assertEqual(p['metrics']['total_return'],0.);self.assertEqual(p['metrics']['max_drawdown']['loss'],0.)

if __name__=='__main__':unittest.main()
