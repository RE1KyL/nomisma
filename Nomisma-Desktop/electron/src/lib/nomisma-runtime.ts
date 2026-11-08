import {app} from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import {checkedDirectory, verifyWindowsRuntime} from './windows-runtime.cjs';

const bundledRoot = () => path.join(process.resourcesPath, 'nomisma');
export const installedNomisma = () => app.isPackaged && process.platform === 'win32';
export const nomismaProjectRoot = () => installedNomisma() ? path.join(bundledRoot(), 'research')
  : process.env.NOMISMA_PROJECT_ROOT ?? path.resolve(app.getAppPath(), '../Nomisma');
export const nomismaPython = () => installedNomisma() ? path.join(bundledRoot(), 'python', 'python.exe')
  : 'D:/AKSHARE/envs/nomisma/python.exe';
export const nomismaNode = () => installedNomisma() ? path.join(bundledRoot(), 'node', 'node.exe') : 'D:/node.exe';
export const nomismaPythonCwd = () => installedNomisma() ? path.dirname(nomismaPython()) : undefined;

export function configureInstalledNomisma(): void {
  if (process.platform !== 'win32') throw new Error('Nomisma release runtime requires Windows');
  const local = process.env.LOCALAPPDATA;
  const windows = process.env.SystemRoot ?? process.env.SYSTEMROOT;
  if (!local || !path.isAbsolute(local) || !windows || !path.isAbsolute(windows)) {
    throw new Error('Windows local application data and system directories are required');
  }
  const metadata = JSON.parse(fs.readFileSync(path.join(app.getAppPath(), 'package.json'), 'utf8'));
  verifyWindowsRuntime(bundledRoot(), metadata.nomismaRuntimeManifestSha256, process.arch);

  // Never reuse the development or upstream profiles. Keep the existing registry
  // layout within this separate root so Python and ACP read the same state.
  let state = path.resolve(local);
  checkedDirectory(state);
  for (const name of ['NomismaRelease', 'Nomisma', 'desktop-dev']) {
    state = path.join(state, name);
    try { fs.mkdirSync(state); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error; }
    checkedDirectory(state);
  }
  app.setPath('userData', state);
  app.setPath('sessionData', state);
  process.env.NOMISMA_STATE_ROOT = path.dirname(state);
  process.env.NOMISMA_DESKTOP_MODE = 'isolated';
  process.env.NOMISMA_FILE_TEST = '0';
  delete process.env.NOMISMA_PROJECT_ROOT;

  // Allow bundled Python DLL lookup without inheriting executable search paths
  // from the launching shell or a developer's Conda installation.
  const python = path.dirname(nomismaPython());
  process.env.PATH = [python, path.join(python, 'DLLs'), path.join(python, 'Library', 'bin'),
    path.join(windows, 'System32'), windows].join(path.delimiter);
}
