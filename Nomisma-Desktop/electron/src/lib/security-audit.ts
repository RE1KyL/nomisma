type Source = 'ipc' | 'model' | 'connector' | 'credential';
type Status = 'allowed' | 'denied' | 'completed' | 'failed';
type Purpose = 'capability' | 'inference' | 'probe' | 'directory' | 'migration' | 'public-data';
type Provider = 'openai' | 'anthropic' | 'deepseek' | 'custom';

interface Receipt {
  at: string;
  source: Source;
  purpose: Purpose;
  status: Status;
  provider?: Provider;
}

const receipts: Receipt[] = [];

/** Keep bounded event metadata. Payloads, URLs and paths are excluded by construction. */
export function recordSecurityEvent(source: Source, purpose: Purpose, status: Status, provider?: Provider): void {
  receipts.push({at: new Date().toISOString(), source, purpose, status, ...(provider ? {provider} : {})});
  if (receipts.length > 256) receipts.shift();
}

export function securityDiagnostics(): { version: 1; events: Receipt[] } {
  return {version: 1, events: receipts.map(event => ({...event}))};
}
