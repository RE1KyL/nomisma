import type {Stats} from 'node:fs';
export function checkedDirectory(directory: string): void;
export function isPathRedirected(file: string, entry?: Stats): boolean;
export function verifyWindowsRuntime(root: string, expectedDigest: string | undefined, arch: string): void;
export function writeWindowsRuntimeManifest(root: string, arch: string): string;
