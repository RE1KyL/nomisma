import numpy as np
import pandas as pd
from _bootstrap import AnalysisTest
from finance_timeseries import rolling_statistics,moving_average,return_distribution,asset_correlations


class TimeSeriesTests(AnalysisTest):
    def call(self,fn,frame,**kwargs):
        source=self.dataset(frame)
        return fn(self.store,source.metadata['dataset_id'],expected_sha256=source.metadata['sha256'],date_column='date',frequency='daily',**kwargs)

    def frame(self,values):return pd.DataFrame({'date':pd.date_range('2025-01-01',periods=len(values)),'x':values})

    def test_trailing_fixed_mean_std_and_quantiles(self):
        f=self.frame([1.,2.,3.,4.]);common={'value_column':'x','value_kind':'measurement','window':3,'min_periods':3}
        a=self.call(rolling_statistics,f,statistic='mean',**common).tables['timeseries']
        self.assertTrue(a.value.iloc[:2].isna().all());np.testing.assert_allclose(a.value.iloc[2:],[2.,3.])
        a=self.call(rolling_statistics,f,statistic='std',**common).tables['timeseries'];np.testing.assert_allclose(a.value.iloc[2:],[1.,1.])
        a=self.call(rolling_statistics,f,statistic='quantile',quantile=.25,**common).tables['timeseries'];np.testing.assert_allclose(a.value.iloc[2:],[1.5,2.5])

    def test_ema_adjust_false_and_true_are_explicit_and_correct(self):
        f=self.frame([1.,2.,3.]);common={'value_column':'x','value_kind':'measurement','window':3,'min_periods':1,'kind':'ema'}
        a=self.call(moving_average,f,adjust=False,**common).tables['timeseries'];np.testing.assert_allclose(a.value,[1.,1.5,2.25])
        b=self.call(moving_average,f,adjust=True,**common).tables['timeseries'];np.testing.assert_allclose(b.value,[1.,5/3,17/7])
        self.code('INVALID_ARGUMENT',lambda:self.call(moving_average,f,**common))

    def test_appending_future_rows_cannot_change_past_rolling_or_ema(self):
        base=self.frame([1.,2.,3.,4.]);future=self.frame([1.,2.,3.,4.,999.,-555.])
        for fn,kwargs in [(rolling_statistics,{'statistic':'std'}),(moving_average,{'kind':'ema','adjust':False})]:
            common={'value_column':'x','value_kind':'measurement','window':3,'min_periods':2,**kwargs}
            a=self.call(fn,base,**common).tables['timeseries'];b=self.call(fn,future,**common).tables['timeseries']
            np.testing.assert_allclose(a.value,b.value.iloc[:4],equal_nan=True)

    def test_preserved_missing_values_report_actual_window_n(self):
        a=self.call(rolling_statistics,self.frame([1.,np.nan,3.,5.]),value_column='x',value_kind='measurement',
            window=3,min_periods=2,statistic='mean',missing_policy='preserve').tables['timeseries']
        self.assertEqual(a.valid_window_count.tolist(),[1,1,2,2]);self.assertAlmostEqual(a.value.iloc[2],2)
        self.code('MISSING_VALUES',lambda:self.call(moving_average,self.frame([1.,np.nan,3.]),value_column='x',value_kind='measurement',
            window=3,min_periods=2,kind='ema',adjust=False))

    def test_distribution_histogram_counts_and_sample_statistics(self):
        a=self.call(return_distribution,self.frame([-.2,-.1,.1,.2]),return_column='x',return_kind='simple',bins=2)
        self.assertEqual(a.tables['histogram']['count'].tolist(),[2,2]);self.assertAlmostEqual(a.computation['assets'][0]['statistics']['mean'],0)

    def test_rolling_correlation_has_pair_mask_and_no_future(self):
        f=self.frame([1.,2.,3.,4.]);f['y']=[2.,4.,6.,8.]
        a=self.call(rolling_statistics,f,value_column='x',other_column='y',value_kind='measurement',window=3,
            min_periods=2,statistic='correlation').tables['timeseries']
        np.testing.assert_allclose(a.value.iloc[1:],[1.,1.,1.],atol=1e-12)

    def test_asset_correlation_aligns_dates_instead_of_row_numbers(self):
        # Non-matching first/last dates. Correct overlap is two pairs (2,2),(3,3), r=1.
        f=pd.DataFrame({'date':['2025-01-01','2025-01-02','2025-01-03','2025-01-02','2025-01-03','2025-01-04'],
            'asset':['a']*3+['b']*3,'r':[.1,.2,.3,.2,.3,-.9]})
        a=self.call(asset_correlations,f,asset_column='asset',return_column='r',return_kind='simple',assets=['a','b'],missing_policy='pairwise')
        pair=a.computation['correlation']['pairs'][0];self.assertEqual(pair['n'],2);self.assertAlmostEqual(pair['coefficient'],1)
        self.assertIsNone(pair['p_value']);self.assertEqual(pair['start'][:10],'2025-01-02')
        self.code('MISSING_VALUES',lambda:self.call(asset_correlations,f,asset_column='asset',return_column='r',return_kind='simple',assets=['a','b']))

    def test_unknown_statistic_or_centering_proxy_is_not_accepted(self):
        self.code('INVALID_ARGUMENT',lambda:self.call(rolling_statistics,self.frame([1.,2.,3.]),value_column='x',value_kind='measurement',
            window=3,min_periods=2,statistic='eval'))
