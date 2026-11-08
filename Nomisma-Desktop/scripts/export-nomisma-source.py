"""Create a source-only review artifact; never publish or include user state."""
import hashlib
import json
from pathlib import Path
import re
import shutil
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'release-source/nomisma'
ARCHIVE = ROOT / 'release-source/nomisma-mit-source.zip'
plan = {}
forbidden_parts = {'.git', 'node_modules', '__pycache__', '.test-tmp', 'logs', 'release', 'dist'}
forbidden_suffixes = {'.exe', '.dll', '.node', '.pyd', '.pyc', '.pfx', '.p12', '.key', '.pem', '.log', '.jsonl', '.sqlite', '.db'}


def add(file):
    relative = file.relative_to(ROOT)
    if any(part in forbidden_parts for part in relative.parts) or file.suffix.lower() in forbidden_suffixes:
        return
    if relative.as_posix().startswith('Nomisma/workspace/'):
        return
    if file.is_symlink() or file.resolve() != ROOT.resolve() / relative:
        raise ValueError(f'Redirected source: {relative}')
    if file.name == 'nomisma-agent.example.json' or file.name.startswith('.env'):
        return
    if file.stat().st_size > 10 * 1024 * 1024:
        raise ValueError(f'Oversized source file: {relative}')
    plan[relative.as_posix()] = file


def tree(relative, extensions=None):
    directory = ROOT / relative
    if not directory.exists():
        return
    for file in sorted(directory.rglob('*')):
        if file.is_file() and (extensions is None or file.suffix.lower() in extensions):
            add(file)


for name in ['README.md', 'LICENSE', 'SECURITY.md', 'THIRD_PARTY_NOTICES.md', '.gitignore']:
    add(ROOT / name)
for name in ['.gitignore', '.npmrc', 'components.json', 'electron-builder.config.js', 'index.html',
             'LICENSE', 'package.json', 'pnpm-lock.yaml', 'pnpm-workspace.yaml', 'README.md',
             'README.upstream.md', 'UPSTREAM_ORIGIN.md', 'tsconfig.json', 'tsup.electron.config.ts',
             'vite.config.ts', 'vitest.config.electron.ts']:
    add(ROOT / 'Nomisma-Desktop' / name)
for directory in ['electron/src', 'src', 'shared', 'public', 'build']:
    tree(f'Nomisma-Desktop/{directory}')
tree('Nomisma-Desktop/scripts', {'.js', '.cjs', '.mjs', '.py', '.sh', '.json', '.yaml', '.md'})
for name in ['README.md', 'environment.yml']:
    add(ROOT / 'Nomisma' / name)
tree('Nomisma/nomisma_tools', {'.py'})
tree('Nomisma/launcher', {'.py', '.cjs', '.mjs', '.json', '.ps1'})
tree('Nomisma/knowledge', {'.md', '.json', '.yml', '.yaml'})
tree('Nomisma/.dsh/skills', {'.md', '.json', '.py', '.yml', '.yaml'})
for directory in sorted((ROOT / 'Nomisma/tests').iterdir()):
    if directory.is_dir() and directory.name.startswith('phase'):
        for file in sorted(directory.glob('test*.py')):
            add(file)
tree('Nomisma/tests/phase1b2/fixture')

patterns = {
    'private-key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'github-token': re.compile(rb'gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,}'),
    'aws-access-key': re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'provider-secret': re.compile(rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b'),
}
findings = []
for relative, file in sorted(plan.items()):
    data = file.read_bytes()
    for kind, pattern in patterns.items():
        if pattern.search(data):
            findings.append({'file': relative, 'kind': kind})
if findings:
    raise ValueError('Credential-pattern review required (values redacted): ' + json.dumps(findings))
assert 'Nomisma-Desktop/LICENSE' in plan and 'LICENSE' in plan
assert not OUT.exists() and not ARCHIVE.exists(), 'Preserve the previous review artifact; use a clean output directory'
OUT.mkdir(parents=True)
entries = []
for relative, source in sorted(plan.items()):
    target = OUT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    data = target.read_bytes()
    assert data == source.read_bytes(), relative
    entries.append({'path': relative, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
manifest = {'license': 'MIT', 'artifact': 'source-only', 'signing_certificate_required': False,
            'files': entries, 'credential_pattern_matches': 0,
            'scope': 'Allowlisted source and synthetic fixtures; patterns cannot guarantee detection of every possible secret'}
(OUT / 'SOURCE_MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
with ZipFile(ARCHIVE, 'w', compression=ZIP_DEFLATED) as bundle:
    for file in sorted(OUT.rglob('*')):
        if file.is_file():
            bundle.write(file, file.relative_to(OUT).as_posix())
with ZipFile(ARCHIVE) as bundle:
    assert bundle.testzip() is None
    for entry in entries:
        assert hashlib.sha256(bundle.read(entry['path'])).hexdigest() == entry['sha256']
print(json.dumps({'status': 'SOURCE_ARTIFACT_REVIEW_PASS', 'files': len(entries), 'credential_pattern_matches': 0,
                  'archive': str(ARCHIVE.relative_to(ROOT)), 'archive_sha256': hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
                  'remote_publication': 'NOT_PERFORMED', 'signing_certificate_required': False}))
