import {expect,it,vi} from 'vitest';
import {continueResearchTurn} from '../research-continuation';
it('continues only when real task progress exposes another ready step',async()=>{
  const refresh=vi.fn().mockResolvedValueOnce({managed:true,status:'active',tasks:['first_succeeded','second_ready'],ready_tasks:[{tool:'actual_tool',arguments:{}}]})
    .mockResolvedValueOnce({managed:true,status:'computed',tasks:['first_succeeded','second_succeeded'],ready_tasks:[]});
  const next=vi.fn(async()=>({stopReason:'end_turn'}));
  await continueResearchTurn({result:{stopReason:'end_turn'},before:{managed:true,tasks:['first_ready','second_pending']},refresh,next,stopped:()=>false});
  expect(next).toHaveBeenCalledTimes(1);expect(refresh).toHaveBeenCalledTimes(2);
});
it('does not repeat a clarification turn or continue a paused/cancelled research',async()=>{
  for(const status of ['active','paused','recovery_required','cancelled']){
    const next=vi.fn();await continueResearchTurn({result:{stopReason:'end_turn'},before:{managed:true,tasks:['same']},
      refresh:async()=>({managed:true,status,tasks:['same'],ready_tasks:[{}]}),next,stopped:()=>false});expect(next).not.toHaveBeenCalled();
  }
});
it('honors Stop before requesting another model turn',async()=>{
  const refresh=vi.fn(),next=vi.fn();
  await continueResearchTurn({result:{stopReason:'end_turn'},before:{managed:true},refresh,next,stopped:()=>true});
  expect(next).not.toHaveBeenCalled();expect(refresh).not.toHaveBeenCalled();
});
