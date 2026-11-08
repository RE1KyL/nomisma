// DSH loads its own credential provider; this adapter never returns credential
// values to application code and refuses every persistence/migration operation.
import { LocalCredentialProvider } from 'file:///D:/DSK%20HARNS/resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-credentials-local/lib/index.js';

class ReadonlyCredentials extends LocalCredentialProvider {
  async migrateFlatDocument() { throw new Error('NOMISMA_READONLY_MIGRATION'); }
  async set() { throw new Error('NOMISMA_READONLY_CREDENTIALS'); }
  async unset() { throw new Error('NOMISMA_READONLY_CREDENTIALS'); }
  async modifyRecord() { throw new Error('NOMISMA_READONLY_CREDENTIALS'); }
  async deleteRecord() { throw new Error('NOMISMA_READONLY_CREDENTIALS'); }
  async write() { throw new Error('NOMISMA_READONLY_CREDENTIALS'); }
}

export const name = 'nomisma-readonly-credentials';
export const Config = LocalCredentialProvider.Config;
export async function apply(ctx, config) {
  const provider = new ReadonlyCredentials(ctx, config);
  await provider.loadInitial();
}
