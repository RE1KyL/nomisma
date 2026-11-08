"""3A-4: finite long-only, cash-funded A-share daily-open scenario engine.

Signals precede execution. Capacity is supplied with pre-open availability, NOT
inferred from the day's eventual volume. Every state/fee/action is explicit.
Strict historical certification remains unavailable with current candidate data.
"""
import math
import numpy as np
import pandas as pd
from ashare_data import columns,day,timestamp,number,fail
from quant_factors import validate_sessions

VERSION='quant-backtest-1'

def simulate(bars,targets,market_state,calendar,fee_schedule,actions,*,initial_cash,slippage_bps,
             stale_price_policy,execution_mode,initial_positions=None,cancelled=None):
    if execution_mode!='declared_scenario':
        fail('DATA_NOT_CERTIFIED','Current adapters cannot certify a strict historical trading replay')
    if stale_price_policy not in ('reject','carry_suspended'):fail('INVALID_ARGUMENT','An explicit stale valuation policy is required')
    if initial_positions:fail('UNSUPPORTED_INITIAL_STATE','Initial holdings need acquisition lots and entitlement history; start with explicit cash')
    cash=number(initial_cash,positive=True);initial=cash;slip=number(slippage_bps,nonnegative=True)/10000
    if slip>.05:fail('INVALID_ARGUMENT','Slippage exceeds the bounded scenario range')
    columns(calendar,['session','open_at','close_at'],('session',));cal=calendar.sort_values('session').copy()
    sessions=cal.session.tolist();ix=validate_sessions(sessions)
    for r in cal.to_dict('records'):
        if (timestamp(r['open_at'])>=timestamp(r['close_at']) or
            timestamp(r['open_at']).tz_convert('Asia/Shanghai').date().isoformat()!=r['session'] or
            timestamp(r['close_at']).tz_convert('Asia/Shanghai').date().isoformat()!=r['session']):
            fail('INVALID_CALENDAR','Open and close must belong to their declared session')
    columns(bars,['session','security_id','open','close','price_basis'],('session','security_id'))
    if set(bars.price_basis)!={'unadjusted'}:fail('PRICE_BASIS_INVALID','Execution and corporate actions require unadjusted prices')
    if not set(bars.session)<=set(sessions):fail('INVALID_CALENDAR','Bars outside the simulation calendar')
    columns(targets,['signal_session','execution_session','formation_at','security_id','target_weight'],('execution_session','security_id'))
    assets=sorted(set(bars.security_id)|set(targets.security_id))
    if not 1<=len(assets)<=100:fail('RESOURCE_LIMIT','Simulation supports one to one hundred explicit securities')
    if len(sessions)*len(assets)>100000:fail('RESOURCE_LIMIT','Simulation exceeds the existing table row budget')
    required=['session','security_id','listed','suspended','can_buy','can_sell','known_at','rule_version','evidence_id',
              'lot_size','t_plus_one','odd_lot_liquidation','limit_policy','capacity_shares','capacity_available_at']
    columns(market_state,required,('session','security_id'))
    if not {'limit_up','limit_down'}<=set(market_state):fail('SCHEMA_MISMATCH','Limit prices must be nullable explicit fields')
    states=market_state.set_index(['session','security_id']);prices=bars.set_index(['session','security_id'])
    columns(fee_schedule,['effective_from','known_at','commission_rate','minimum_commission','stamp_sell_rate','transfer_rate','rule_version'])
    if 'effective_to' not in fee_schedule:fail('SCHEMA_MISMATCH','Fee intervals require nullable effective_to')
    # Action table may be empty, but its schema is still explicit.
    action_fields={'event_id','security_id','kind','session','known_at','evidence_id'}
    if not isinstance(actions,pd.DataFrame) or not action_fields<=set(actions):fail('SCHEMA_MISMATCH','Explicit action schema required')
    if actions.event_id.duplicated().any():fail('DUPLICATE_KEY','Action IDs repeat')
    if not set(actions.security_id)<=set(assets) or not set(actions.session)<=set(sessions):fail('ACTION_OUT_OF_SCOPE','Actions must belong to the simulated universe/calendar')
    if not set(actions.kind)<={'split','cash_dividend','delisting'}:fail('ACTION_NOT_SUPPORTED','Unknown corporate action')
    for r in targets.to_dict('records'):
        if r['signal_session'] not in ix or r['execution_session'] not in ix or ix[r['execution_session']]<=ix[r['signal_session']]:
            fail('LOOKAHEAD_RISK','Execution must be a future calendar session')
        signal=cal[cal.session==r['signal_session']].iloc[0];formed=timestamp(r['formation_at'])
        if formed<timestamp(signal.close_at) or formed>=timestamp(cal[cal.session==r['execution_session']].iloc[0].open_at):
            fail('LOOKAHEAD_RISK','Signal formation must be after signal close and before execution open')
        if not 0<=number(r['target_weight'])<=1:fail('INVALID_WEIGHTS','Target weight must lie in [0,1]')
    for _,g in targets.groupby('execution_session'):
        if set(g.security_id)!=set(assets) or g.target_weight.map(number).sum()>1+1e-12:
            fail('INVALID_WEIGHTS','Each rebalance declares every asset, explicit zero sales and residual cash')
    lots={a:[] for a in assets};last={};record_shares={};receivables={};terminated=set()
    ledgers={name:[] for name in ('orders','fills','positions','cash','corporate_actions','nav','rejections')}
    def shares(a):return sum(l['quantity'] for l in lots[a])
    def reject(session,a,side,quantity,reason):
        ledgers['rejections'].append({'session':session,'security_id':a,'side':side,'requested_quantity':quantity,'reason':reason})
    def take(a,quantity,t):
        remain=quantity
        for lot in lots[a]:
            if lot['release_index']<=t:
                sold=min(remain,lot['quantity']);lot['quantity']-=sold;remain-=sold
        lots[a]=[l for l in lots[a] if l['quantity']>1e-9]
        if remain>1e-8:fail('POSITION_INCONSISTENT','Sale exceeded settled holdings')
    for t,c in enumerate(cal.to_dict('records')):
        if cancelled and cancelled():fail('CANCELLED','Backtest cancelled; no completed result')
        session=c['session'];op=timestamp(c['open_at']);state={};opening={};stale={}
        # Fees are historical effective versions, not current rates backfilled.
        fees=fee_schedule[(fee_schedule.effective_from<=session)&(fee_schedule.effective_to.isna()|(fee_schedule.effective_to>session))]
        fees=fees[fees.known_at.map(timestamp)<=op]
        if len(fees)!=1:fail('FEE_SCHEDULE_UNKNOWN','No unique fee version was known at execution')
        fee=fees.iloc[0]
        rates={n:number(fee[n],nonnegative=True) for n in ('commission_rate','minimum_commission','stamp_sell_rate','transfer_rate')}
        if any(rates[n]>.1 for n in ('commission_rate','stamp_sell_rate','transfer_rate')):fail('INVALID_FEES','Fee rate outside scenario range')
        def charge(notional,side):
            return max(rates['minimum_commission'],notional*rates['commission_rate'])+notional*rates['transfer_rate']+(notional*rates['stamp_sell_rate'] if side=='sell' else 0.)
        for a in assets:
            key=(session,a)
            if key not in states.index:fail('TRADING_STATE_UNKNOWN','Missing state is not treated as tradable')
            s=states.loc[key].copy()
            for name in ('listed','suspended','can_buy','can_sell','t_plus_one','odd_lot_liquidation'):
                if type(s[name]) not in (bool,np.bool_):fail('TRADING_STATE_UNKNOWN','Trading flags must be explicit booleans')
            if timestamp(s.known_at)>op or timestamp(s.capacity_available_at)>op:
                fail('LOOKAHEAD_RISK','Execution rules or capacity were not known before open')
            if type(s.lot_size) not in (int,np.int64,np.int32) or not 1<=s.lot_size<=1000:
                fail('INVALID_LOT_SIZE','Explicit integer lot size required')
            number(s.capacity_shares,nonnegative=True)
            if s.limit_policy not in ('bounded','none'):fail('LIMIT_RULE_UNKNOWN','Price-limit policy is unknown')
            if s.limit_policy=='bounded':
                s['limit_down']=number(s.limit_down,positive=True);s['limit_up']=number(s.limit_up,positive=True)
                if s.limit_down>=s.limit_up:fail('INVALID_LIMITS','Limit bounds are invalid')
            state[a]=s
            if key in prices.index:opening[a]=number(prices.loc[key].open,positive=True);stale[a]=False
            elif shares(a)>0:
                terminal=actions[(actions.security_id==a)&(actions.session==session)&(actions.kind=='delisting')]
                if len(terminal)==1:
                    opening[a]=number(terminal.iloc[0].get('settlement_price'),nonnegative=True);stale[a]=False
                elif s.suspended and stale_price_policy=='carry_suspended' and a in last:opening[a]=last[a];stale[a]=True
                else:fail('VALUATION_MISSING','No valid opening price for existing holdings')
        # Realized cash events are applied on their actual dates, never anticipated by the signal.
        for r in actions[actions.session==session].to_dict('records'):
            a=r['security_id']
            if timestamp(r['known_at'])>op:fail('ACTION_TIME_UNKNOWN','Action effective before its recorded availability')
            amount=0.
            if r['kind']=='split':
                ratio=number(r.get('ratio'),positive=True)
                if ratio<1:fail('ACTION_NOT_SUPPORTED','Reverse splits require a fractional-share settlement model')
                release=r.get('sellable_session')
                if release not in ix or ix[release]<t:fail('ACTION_TIME_UNKNOWN','Bonus-share listing session is required')
                for lot in list(lots[a]):
                    bonus=lot['quantity']*(ratio-1)
                    if abs(bonus-round(bonus))>1e-8:fail('FRACTIONAL_SHARES','Fractional bonus shares need explicit settlement')
                    if bonus:lots[a].append({'quantity':float(round(bonus)),'release_index':max(lot['release_index'],ix[release])})
                if (session,a) not in prices.index and a in last:last[a]/=ratio;opening[a]=last[a]
            elif r['kind']=='cash_dividend':
                record=r.get('record_session');pay=r.get('pay_session');per_share=number(r.get('cash_per_share'),nonnegative=True)
                tax=number(r.get('tax_rate'))
                if not 0<=tax<=1 or record not in ix or ix[record]>=t or pay not in ix or ix[pay]<t:
                    fail('ACTION_TIME_UNKNOWN','Dividend record/ex/pay alignment and tax must be explicit')
                if (record,a) not in record_shares:fail('ENTITLEMENT_UNKNOWN','Record-date holdings are unavailable')
                amount=record_shares[(record,a)]*per_share*(1-tax)
                receivables[r['event_id']]={'amount':amount,'pay_session':pay,'security_id':a}
                if shares(a)>0 and (session,a) not in prices.index:
                    last[a]=number(r.get('ex_reference_price'),positive=True);opening[a]=last[a]
            else:
                settlement=number(r.get('settlement_price'),nonnegative=True)
                amount=shares(a)*settlement;cash+=amount;lots[a]=[];terminated.add(a)
                if state[a].listed:fail('TRADING_STATE_CONFLICT','Delisted asset still marked listed')
            ledgers['corporate_actions'].append({'event_id':r['event_id'],'session':session,'security_id':a,
                'kind':r['kind'],'cash_or_receivable':amount,'evidence_id':r['evidence_id']})
        for event,r in list(receivables.items()):
            if r['pay_session']==session:
                cash+=r['amount'];ledgers['cash'].append({'session':session,'event':'dividend_payment','amount':r['amount'],'balance':cash,'event_id':event})
                del receivables[event]
        for a in assets:
            if not state[a].listed and shares(a)>0:fail('DELISTING_SETTLEMENT_MISSING','Holdings cannot disappear at an unknown delisting value')
        open_equity=cash+sum(shares(a)*opening[a] for a in assets if shares(a)>0)+sum(r['amount'] for r in receivables.values())
        todays=targets[targets.execution_session==session];desired={}
        for r in todays.to_dict('records'):
            a=r['security_id'];s=state[a];weight=number(r['target_weight'])
            if a in terminated or not s.listed:
                if weight>0:reject(session,a,'buy',0,'NOT_LISTED')
                desired[a]=0.;continue
            if a not in opening:
                if weight>0:reject(session,a,'buy',0,'OPEN_PRICE_MISSING')
                desired[a]=shares(a);continue
            desired[a]=math.floor(open_equity*weight/opening[a]/int(s.lot_size))*int(s.lot_size)
        # Sells first, then deterministic security-ID order buys. No ex-post priority sorting.
        for side in ('sell','buy'):
            for a in sorted(desired):
                held=shares(a);delta=desired[a]-held
                if side=='sell' and delta>=-1e-9 or side=='buy' and delta<=1e-9:continue
                requested=abs(delta);s=state[a];why=None
                if not s.listed or a in terminated:why='NOT_LISTED'
                elif s.suspended:why='SUSPENDED'
                elif not s['can_'+side]:why='SIDE_NOT_ALLOWED'
                elif a not in opening:why='OPEN_PRICE_MISSING'
                price=None if a not in opening else opening[a]*(1+slip if side=='buy' else 1-slip)
                if why is None and s.limit_policy=='bounded':
                    if side=='buy' and (opening[a]>=s.limit_up-1e-10 or price>s.limit_up+1e-10):why='UP_LIMIT_CONSERVATIVE_REJECTION'
                    if side=='sell' and (opening[a]<=s.limit_down+1e-10 or price<s.limit_down-1e-10):why='DOWN_LIMIT_CONSERVATIVE_REJECTION'
                ledgers['orders'].append({'session':session,'security_id':a,'side':side,'requested_quantity':requested,
                    'target_weight':float(todays[todays.security_id==a].iloc[0].target_weight),'rule_version':s.rule_version})
                if why:reject(session,a,side,requested,why);continue
                capacity=math.floor(number(s.capacity_shares,nonnegative=True));quantity=min(requested,capacity)
                lot=int(s.lot_size)
                if side=='sell':
                    settled=sum(l['quantity'] for l in lots[a] if l['release_index']<=t);quantity=min(quantity,settled)
                    if quantity<held-1e-9 or not s.odd_lot_liquidation:quantity=math.floor(quantity/lot)*lot
                else:
                    quantity=math.floor(quantity/lot)*lot
                    # Binary-search cash-feasible integer lots, including minimum commission.
                    lo=0;hi=int(quantity/lot)
                    while lo<hi:
                        mid=(lo+hi+1)//2;cost=mid*lot*price
                        if cost+charge(cost,'buy')<=cash+1e-10:lo=mid
                        else:hi=mid-1
                    quantity=lo*lot
                if quantity<=0:reject(session,a,side,requested,'NO_SETTLED_LOT_CAPACITY_OR_CASH');continue
                notional=quantity*price;cost=charge(notional,side)
                if side=='sell':take(a,quantity,t);cash+=notional-cost
                else:
                    cash-=notional+cost;lots[a].append({'quantity':float(quantity),'release_index':t+1 if s.t_plus_one else t})
                if cash<-1e-8:fail('CASH_INCONSISTENT','Negative cash is not allowed')
                ledgers['fills'].append({'session':session,'security_id':a,'side':side,'quantity':quantity,
                    'price':price,'open_price':opening[a],'notional':notional,'fees':cost,
                    'slippage_cost':abs(price-opening[a])*quantity,'capacity_basis':'preopen_declared',
                    'fee_rule_version':fee.rule_version,'cash_after':cash})
                ledgers['cash'].append({'session':session,'event':side,'amount':(-notional-cost if side=='buy' else notional-cost),'balance':cash,'event_id':None})
                if quantity<requested:reject(session,a,side,requested-quantity,'PARTIAL_FILL_REMAINDER_NOT_RETRIED')
        market_value=0.
        for a in assets:
            qty=shares(a);record_shares[(session,a)]=qty
            if qty<=0:continue
            key=(session,a)
            if key in prices.index:close=number(prices.loc[key].close,positive=True);last[a]=close;is_stale=False
            elif state[a].suspended and stale_price_policy=='carry_suspended' and a in last:close=last[a];is_stale=True
            else:fail('VALUATION_MISSING','Closing valuation for held assets is missing')
            market_value+=qty*close
            ledgers['positions'].append({'session':session,'security_id':a,'quantity':qty,'close':close,
                'market_value':qty*close,'sellable_next_session':sum(l['quantity'] for l in lots[a] if l['release_index']<=t+1),
                'stale_valuation':is_stale})
        receivable_value=sum(r['amount'] for r in receivables.values());equity=cash+market_value+receivable_value
        if not math.isfinite(equity) or equity<0:fail('NAV_INCONSISTENT','Invalid equity')
        ledgers['nav'].append({'session':session,'cash':cash,'market_value':market_value,'receivables':receivable_value,
            'equity':equity,'nav':equity/initial})
    result={k:pd.DataFrame(v) for k,v in ledgers.items()};result['targets']=targets.copy()
    result['summary']={'version':VERSION,'execution_mode':execution_mode,'initial_cash':initial,'final_equity':equity,
        'total_fees':float(sum(f['fees'] for f in ledgers['fills'])),'total_slippage_cost':float(sum(f['slippage_cost'] for f in ledgers['fills'])),
        'strict_backtest_ready':False,'limitations':['Daily-open declared scenario, not historical queue reconstruction',
          'Fee/tax/lot/capacity/state rules must be supplied with historical evidence',
          'Cash dividends use explicit flat scenario tax; holding-period tax reconciliation is not implemented',
          'Outstanding receivables are marked at face value; no interest is accrued']}
    return result
