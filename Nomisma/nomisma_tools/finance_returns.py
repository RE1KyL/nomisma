"""Adjacent price returns; no forward fill, implicit cleaning or Dataset registration."""
import numpy as np
import pandas as pd
from finance_common import load_panel,numeric,finite,conventions,finish
from dataset_store import DatasetError


def analyze_returns(store,dataset_id,*,expected_sha256,date_column,price_column,frequency,
                    price_adjustment,asset_column=None,return_kind='simple',missing_policy='reject',periods_per_year=None):
    convention=conventions(frequency,periods_per_year)
    if (return_kind not in ('simple','log') or missing_policy not in ('reject','strict_adjacent')
            or price_adjustment not in ('unknown','unadjusted','forward_adjusted','backward_adjusted','index_level')):
        raise DatasetError('INVALID_FINANCIAL_CONVENTION','Price basis, return kind and adjacent missing policy must be explicit')
    sample,groups=load_panel(store,dataset_id,expected_sha256,date_column=date_column,value_columns=[price_column],asset_column=asset_column)
    rows=[];summaries=[]
    for asset,frame in groups:
        prices=numeric(frame,price_column,allow_missing=missing_policy=='strict_adjacent')
        if np.any(prices[~np.isnan(prices)]<=0):raise DatasetError('INVALID_PRICE','Observed prices must be strictly positive')
        dates=frame[date_column].tolist();positions=list(frame.index);wealth=1.;chain=not np.isnan(prices[0]);valid=0
        for i,price in enumerate(prices):
            r=simple=np.nan;reason='FIRST_PRICE' if i==0 else None
            if i==0 and np.isnan(price):reason='BASELINE_PRICE_MISSING'
            if i>0:
                if np.isnan(price):reason='MISSING_CURRENT_PRICE';chain=False
                elif np.isnan(prices[i-1]):reason='MISSING_PREVIOUS_PRICE';chain=False
                else:
                    try:
                        with np.errstate(over='raise',invalid='raise',divide='raise',under='ignore'):
                            ratio=price/prices[i-1]
                            if ratio<=0:raise DatasetError('NUMERICAL_FAILURE','Positive price ratio underflowed')
                            simple=finite(ratio-1);r=simple if return_kind=='simple' else finite(np.log(ratio))
                            if simple<=-1:raise DatasetError('NUMERICAL_FAILURE','Positive prices underflowed to total loss')
                            if chain:
                                wealth=finite(wealth*ratio)
                                if wealth<=0:raise DatasetError('NUMERICAL_FAILURE','Price wealth underflowed')
                    except FloatingPointError as error:raise DatasetError('NUMERICAL_FAILURE','Price-return calculation overflowed') from error
                    valid+=1
            rows.append({'source_row':int(positions[i]),'date':dates[i],'asset':asset,'price':price,
                'interval_start':dates[i-1] if i else pd.NaT,
                'elapsed_calendar_days':(dates[i]-dates[i-1]).total_seconds()/86400 if i else np.nan,
                'return':r,'simple_return':simple,'reason':reason,'wealth':wealth if chain else np.nan,
                'cumulative_return':wealth-1 if chain else np.nan})
        summaries.append({'asset':asset,'price_observations':len(prices),'valid_return_count':valid,
            'unavailable_count':len(prices)-valid,'full_chain_available':chain,
            'cumulative_return':wealth-1 if chain else None,'wealth_end':wealth if chain else None,
            'start':dates[0].isoformat(),'end':dates[-1].isoformat()})
    computation={'method':'fin_returns','conventions':convention,'return_kind':return_kind,'assets':summaries,
        'price_adjustment':price_adjustment,'return_basis':'price_return_not_verified_total_return',
        'warnings':['PRICE_ADJUSTMENT_IS_DECLARED; DIVIDENDS_AND_TOTAL_RETURN_NOT_VERIFIED',
            'OBSERVATION_ADJACENCY_DOES_NOT_VERIFY_A_TRADING_CALENDAR']}
    parameters={'date_column':date_column,'price_column':price_column,'asset_column':asset_column,
        'return_kind':return_kind,'missing_policy':missing_policy,'price_adjustment':price_adjustment,**convention}
    return finish(sample,parameters,computation,{'returns':pd.DataFrame(rows)})
