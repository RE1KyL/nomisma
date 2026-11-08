"""Explicit rollback of only an unchanged Nomisma project-registry migration."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import uuid


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',required=True,type=Path)
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    directory=args.data_dir.absolute()
    if directory.name!='openacpui-data' or directory.parent.name!='desktop-dev' or directory.parent.parent.name.lower()!='nomisma' or directory.resolve()!=directory:
        raise ValueError('Expected an independent Nomisma data directory')
    if not re.fullmatch(r'projects\.migration-v1-[a-f0-9-]+\.json',args.manifest):
        raise ValueError('Invalid migration manifest name')
    manifest=json.loads((directory/args.manifest).read_text(encoding='utf8'))
    if not re.fullmatch(r'projects\.before-workspace-v1-[a-f0-9-]+\.json',manifest['backup']):
        raise ValueError('Invalid backup name')
    current=directory/'projects.json'
    if hashlib.sha256(current.read_bytes()).hexdigest()!=manifest['after_sha256']:
        raise ValueError('Registry changed after migration; automatic rollback refused to preserve new work')
    backup=directory/manifest['backup']
    if current.is_symlink() or backup.is_symlink():raise ValueError('Registry links are not allowed')
    original=backup.read_bytes()
    json.loads(original.decode('utf-8-sig'))
    print('Rollback preflight passed; only projects.json is affected. Project directories and sessions remain in place.')
    if not args.apply:return
    nonce=uuid.uuid4().hex
    with (directory/f'projects.before-rollback-{nonce}.json').open('xb') as f:f.write(current.read_bytes())
    temp=directory/f'.rollback-{nonce}.tmp'
    try:
        with temp.open('xb') as f:f.write(original)
        os.replace(temp,current)
    finally:
        if temp.exists():temp.unlink()
    print('Registry rollback complete; both versions are retained as backups.')


if __name__=='__main__':main()
