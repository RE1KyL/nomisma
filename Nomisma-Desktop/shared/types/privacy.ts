export type PrivacyMode = 'standard' | 'strict' | 'local_only';
export interface PrivacySettings { version:1; mode:PrivacyMode; sensitive_fields?:string[]; }
export interface PrivacyApi { get():Promise<PrivacySettings>; set(mode:PrivacyMode):Promise<PrivacySettings>; setSensitiveFields(fields:string[]):Promise<PrivacySettings>; exportDiagnostics():Promise<boolean>; }
