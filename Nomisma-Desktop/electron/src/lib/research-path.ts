import fs from 'fs';
import path from 'path';
import { getDataDir } from './data-dir';
import { isPathRedirected } from './windows-runtime.cjs';
export function assertNomismaReadPath(candidate: string): void {
  if (process.env.NOMISMA_DESKTOP_MODE !== 'isolated') return;
  const projects: {path:string}[] = JSON.parse(fs.readFileSync(path.join(getDataDir(),'projects.json'),'utf8'));
  for (const project of projects) {
    const relative=path.relative(project.path,path.resolve(candidate));
    if (!relative) { researchPath(project.path,'.'); return; }
    try { researchPath(project.path,relative.split(path.sep).join('/')); return; } catch { /* Try another explicitly registered project. */ }
  }
  throw new Error('File access is restricted to registered research projects');
}
export function researchPath(root: string, relative: string): string {
  if (!relative || path.isAbsolute(relative) || relative.includes('\\') || relative.includes(':') || relative.split('/').includes('..')) throw new Error('Invalid project path');
  const target = path.resolve(root, relative);
  const inside = path.relative(fs.realpathSync.native(root), fs.realpathSync.native(target));
  if (inside.startsWith('..') || path.isAbsolute(inside)) throw new Error('Outside project');
  let item = root;
  for (const segment of ['', ...relative.split('/')]) {
    item = path.join(item, segment);
    if (isPathRedirected(item)) throw new Error('Links are not allowed');
  }
  return target;
}
