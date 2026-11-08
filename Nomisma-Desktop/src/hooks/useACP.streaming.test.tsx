import { renderToStaticMarkup } from 'react-dom/server';
import type { EffectCallback, SetStateAction } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { UIMessage } from '@/types';
import type { ACPSessionEvent, ACPTurnCompleteEvent } from '@/types/acp';

const mock=vi.hoisted(()=>({
  effects:new Array<EffectCallback>(),updates:new Array<SetStateAction<UIMessage[]>>(),messages:new Array<UIMessage>(),
  frames:new Array<()=>void>(),events:new Set<(event:ACPSessionEvent)=>void>(),turns:new Set<(event:ACPTurnCompleteEvent)=>void>(),
  session:{current:'stream-session'},processing:vi.fn(),
}));
vi.mock('react',async original=>({...await original<typeof import('react')>(),useEffect:(effect:EffectCallback)=>mock.effects.push(effect)}));
vi.mock('@/lib/analytics',()=>({captureException:vi.fn()}));
vi.mock('./useEngineBase',()=>({useEngineBase:()=>({
  messages:mock.messages,setMessages:(update:SetStateAction<UIMessage[]>)=>mock.updates.push(update),
  isProcessing:true,setIsProcessing:mock.processing,isConnected:true,setIsConnected:vi.fn(),
  sessionInfo:null,setSessionInfo:vi.fn(),totalCost:0,setTotalCost:vi.fn(),
  pendingPermission:null,setPendingPermission:vi.fn(),contextUsage:null,setContextUsage:vi.fn(),sessionIdRef:mock.session,
  scheduleFlush:(callback:()=>void)=>mock.frames.push(callback),cancelPendingFlush:()=>{mock.frames.length=0;},
})}));
import { useACP } from './useACP';

beforeEach(()=>{
  mock.effects.length=0;mock.updates.length=0;mock.messages.length=0;mock.frames.length=0;
  mock.events.clear();mock.turns.clear();mock.processing.mockClear();mock.session.current='stream-session';
  vi.stubGlobal('window',{claude:{acp:{
    log:vi.fn(),getConfigOptions:vi.fn(async()=>({})),getAvailableCommands:vi.fn(async()=>({})),
    onEvent:(callback:(event:ACPSessionEvent)=>void)=>{mock.events.add(callback);return()=>mock.events.delete(callback);},
    onTurnComplete:(callback:(event:ACPTurnCompleteEvent)=>void)=>{mock.turns.add(callback);return()=>mock.turns.delete(callback);},
    onPermissionRequest:()=>()=>{},onExit:()=>()=>{},
  }}});
  function Probe(){useACP({sessionId:'stream-session'});return null;}
  renderToStaticMarkup(<Probe/>);mock.effects.forEach(effect=>effect());mock.updates.length=0;
});
function text(value:string){
  const event={_sessionId:'stream-session',update:{sessionUpdate:'agent_message_chunk',content:{type:'text',text:value}}} as ACPSessionEvent;
  mock.events.forEach(callback=>callback(event));
}
function finish(reason='end_turn'){
  mock.turns.forEach(callback=>callback({_sessionId:'stream-session',stopReason:reason} as ACPTurnCompleteEvent));
}
function reactBatch(){
  for(const update of mock.updates.splice(0))mock.messages=typeof update==='function'?update(mock.messages):update;
}

it('persists final text and clears streaming when chunk + end_turn arrive before React commits',()=>{
  text('Python 3.11.18，nomisma 环境。');finish();
  // Reproduce React automatic batching: run state updater functions only after
  // finalizeStreamingMessage has reset the mutable ACP buffer.
  reactBatch();
  expect(mock.messages).toHaveLength(1);
  expect(mock.messages[0].content).toBe('Python 3.11.18，nomisma 环境。');
  expect(mock.messages[0].isStreaming).toBe(false);
  expect(mock.processing).toHaveBeenLastCalledWith(false);
});
it('does not mix two finalized replies batched together',()=>{
  text('first ');text('reply');finish();text('second reply');finish();reactBatch();
  expect(mock.messages.map(message=>message.content)).toEqual(['first reply','second reply']);
  expect(mock.messages.every(message=>message.isStreaming===false)).toBe(true);
});
it('preserves partial text on cancelled without fabricating a tool result',()=>{
  text('partial response');finish('cancelled');reactBatch();
  expect(mock.messages[0].content).toBe('partial response');
  expect(mock.messages[0].isStreaming).toBe(false);
  expect(mock.messages[0].toolResult).toBeUndefined();
});
