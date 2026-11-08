import {useEffect,useState} from 'react';
import type {PrivacyMode} from '@shared/types/privacy';
import {reportError} from '@/lib/analytics';
import './model-providers.css';
export function PrivacySettings(){
 const [mode,setMode]=useState<PrivacyMode>('strict'),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [fields,setFields]=useState(''),[saved,setSaved]=useState(false);
 const [dataSource,setDataSource]=useState<'fred'|'bea'>('fred'),[dataKey,setDataKey]=useState(''),[keySaved,setKeySaved]=useState(false);
 useEffect(()=>{void window.privacy.get().then(v=>{setMode(v.mode);setFields((v.sensitive_fields??[]).join(', '));}).catch(()=>setError('无法读取隐私设置；云端访问按受限策略处理。'));},[]);
 async function choose(next:PrivacyMode){setBusy(true);setError('');try{const result=await window.privacy.set(next);setMode(result.mode);}catch{reportError('PRIVACY_SETTINGS',new Error('Privacy setting failed'));setError('保存失败，原有策略继续生效。');}finally{setBusy(false);}}
 async function saveFields(){setBusy(true);setError('');setSaved(false);try{const result=await window.privacy.setSensitiveFields(fields.split(/[,，\s]+/).filter(Boolean));setFields((result.sensitive_fields??[]).join(', '));setSaved(true);}catch{setError('字段名称无效或无法保存。最多 24 个字段，每个名称最多 40 字符。');}finally{setBusy(false);}}
 async function exportDiagnostics(){setBusy(true);setError('');try{await window.privacy.exportDiagnostics();}catch{setError('导出失败，请选择一个尚不存在的文件名。');}finally{setBusy(false);}}
 async function saveDataKey(){
  setBusy(true);setError('');setKeySaved(false);
  try{await window.modelProviders.saveDataCredential(dataSource,dataKey);setDataKey('');setKeySaved(true);}
  catch{setError('无法保存数据源凭据。请检查密钥格式和 Windows 安全存储。');}
  finally{setBusy(false);}
 }
 return <section className="mp-root"><header className="mp-page-heading"><h2>Privacy &amp; Network</h2></header><p>原始研究文件与完整数据集留在本地。研究工具只向模型提供有限的计算摘要，不提供原始文件上传。</p>
 <fieldset disabled={busy} className="mp-editor"><legend>数据与模型策略</legend>{([
 ['standard','Standard Cloud','将主动提交的提示词发送到所选 Provider。额外的会话上下文和工具摘要须先审批。'],
 ['strict','Strict Local Data · 默认','在本地读取和计算。云端请求的工具摘要范围更小，额外上下文须先审批。主动提交的提示词仍会发给云端模型。'],
 ['local_only','Local-only / Offline','阻止云端模型与外部数据连接器；只允许明确配置的回环地址本机模型。没有本地模型时，AI 暂不可用；本地文件与计算仍可使用。'],
 ] as const).map(([value,label,description])=><label key={value}><input type="radio" name="privacy-mode" checked={mode===value} onChange={()=>void choose(value)}/> <strong>{label}</strong><p>{description}</p></label>)}</fieldset>
 <fieldset className="mp-editor" disabled={busy}><legend>云端文本脱敏</legend><label>自定义敏感字段<input value={fields} onChange={event=>{setFields(event.target.value);setSaved(false);}} placeholder="例如 customer_id, 学号"/></label><p>用逗号分隔字段名。云端文本会遮盖可识别的凭证、联系方式及标注字段值。发送前仍应检查敏感信息。</p><button type="button" onClick={()=>void saveFields()}>保存字段</button>{saved&&<p role="status">已保存。</p>}</fieldset>
 <fieldset className="mp-editor" disabled={busy}><legend>安全诊断</legend><p>导出本次运行最近的安全事件，仅包含时间、事件类型、状态及模型服务类型。</p><button type="button" onClick={()=>void exportDiagnostics()}>导出安全事件</button></fieldset>
 <fieldset className="mp-editor" disabled={busy}><legend>经济数据源凭据</legend>
  <label>数据源<select value={dataSource} onChange={event=>{setDataSource(event.target.value==='bea'?'bea':'fred');setDataKey('');setKeySaved(false);}}><option value="fred">FRED</option><option value="bea">BEA</option></select></label>
  <label>API Key<input type="password" autoComplete="new-password" spellCheck={false} value={dataKey} maxLength={36} onChange={event=>{setDataKey(event.target.value);setKeySaved(false);}}/></label>
  <button type="button" disabled={!dataKey.trim()} onClick={()=>void saveDataKey()}>保存到 Windows 安全存储</button>
  {keySaved&&<p role="status">已保存。首次联网获取时验证凭据。</p>}
 </fieldset>
 <p className="mp-note">遥测、自动异常上传和继承的上游自动更新均已禁用。外部链接由您点击后在系统浏览器打开。Standard 也不会自动上传原始文件。</p><p className="mp-note">切换到 Local-only 后，后续外部请求立即阻止；已经发出的请求无法撤回。</p>{error&&<p role="alert" className="mp-error">{error}</p>}</section>;
}
