export type ProviderType = 'openai'|'anthropic'|'deepseek'|'custom';
export type ApiProtocol = 'openai-chat'|'anthropic-messages';
export type CapabilityState = 'passed'|'unsupported'|'untested'|'error'|'not_exposed';
export type Capability = 'text'|'streaming'|'tool_calling'|'structured_output'|'multimodal';
export interface CapabilityProfile {
  adapter: Record<Capability,CapabilityState>;
  model: Record<Capability,CapabilityState>;
  runtime: Record<Capability,CapabilityState>;
  checked_at: string|null;
}
export interface ProviderConfig {
 provider_id:string;provider_name:string;provider_type:ProviderType;api_protocol:ApiProtocol;
 base_url:string;credential_reference:string;model_id:string;display_name:string;
 connection_status:'untested'|'ready'|'limited'|'failed';capability_profile:CapabilityProfile;
 created_at:string;updated_at:string;
}
export interface ProviderDraft {
 provider_id?:string;provider_name:string;provider_type:ProviderType;base_url:string;model_id:string;
 api_key?:string;verification_id?:string;expected_updated_at?:string;
}
export interface ProviderState {providers:ProviderConfig[];default_provider_id:string|null;setup_dismissed:boolean;secure_storage_available:boolean;}
export interface ProbeResult {verification_id:string;ok:boolean;compatible:boolean;capability_profile:CapabilityProfile;error_code?:string;message:string;}
export interface ProviderApi {
 onSetupRequired(callback:()=>void):()=>void;
 list():Promise<ProviderState>;save(draft:ProviderDraft):Promise<ProviderConfig>;
 test(draft:ProviderDraft,optional:boolean):Promise<ProbeResult>;models(draft:ProviderDraft):Promise<string[]>;
 setDefault(id:string):Promise<void>;remove(id:string,confirmed:boolean):Promise<void>;
 reveal(id:string):Promise<void>;dismissSetup():Promise<void>;
 saveDataCredential(source:'fred'|'bea',key:string):Promise<void>;
}
