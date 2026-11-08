import {app} from 'electron';
import fs from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {windowsDpapi,dpapiAvailable} from './windows-dpapi';

export async function saveEconomicCredential(source: unknown, value: unknown): Promise<void> {
  if (source !== 'fred' && source !== 'bea') throw new Error('Unsupported data source');
  const pattern = source === 'fred' ? /^[a-z0-9]{32}$/ : /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i;
  if (typeof value !== 'string' || !pattern.test(value.trim())) throw new Error('Invalid credential');
  if (process.platform !== 'win32' || !dpapiAvailable()) throw new Error('Secure storage unavailable');
  const directory = path.join(process.env.NOMISMA_STATE_ROOT ?? app.getPath('userData'), 'data-source-credentials');
  await fs.mkdir(directory, {recursive:true});
  if (!(await fs.lstat(directory)).isDirectory()) throw new Error('Unsafe credential directory');
  const target = path.join(directory, source + '.secret');
  try {
    if (!(await fs.lstat(target)).isFile()) throw new Error('Unsafe credential file');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  const encrypted = await windowsDpapi('encrypt', Buffer.from(value.trim(), 'ascii'));
  const temporary = path.join(directory, randomUUID() + '.tmp');
  await fs.writeFile(temporary, encrypted, {flag:'wx',mode:0o600,flush:true});
  try {
    await fs.rename(temporary, target);
  } finally {
    await fs.rm(temporary, {force:true});
  }
}
