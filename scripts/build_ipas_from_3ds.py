#!/usr/bin/env python3
"""Package each decrypted 3DS title in a folder, reusing one native Duo shell."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import zipfile

sys.dont_write_bytecode = True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import build_ipa_from_3ds as packer
from three_ds import Dump, DumpError, inspect_dump
from duo_shell import source_digest


def verify_package(path: Path, base: Dump, update: Dump | None) -> dict:
    """Read every ZIP member and check the exact native shell and private inputs."""
    prefix = 'Payload/3dsnative.app/'
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(not n.startswith(prefix) for n in names):
            raise DumpError('Duplicate or unexpected IPA archive paths')
        if any('_CodeSignature' in n or n.endswith('embedded.mobileprovision') for n in names):
            raise DumpError('Unexpected signature resources in unsigned IPA')
        if archive.testzip() is not None:
            raise DumpError('IPA archive CRC failure')
        info = plistlib.loads(archive.read(prefix+'Info.plist'))
        native = json.loads(archive.read(prefix+'native-duo.json'))
        manifest = json.loads(archive.read(prefix+'Game/game.json'))
        expected_id = f'org.threedsnative.title{base.title_id:016x}'
        if info['CFBundleIdentifier'] != expected_id or manifest['title_id'] != f'{base.title_id:016X}':
            raise DumpError('IPA title identity mismatch')
        if native['source_sha256'] != source_digest() or tuple(map(int,native['sdk'].split('.'))) < (27,1):
            raise DumpError('IPA does not contain the current native Duo shell')
        executable = archive.read(prefix+info['CFBundleExecutable'])
        if hashlib.sha256(executable).hexdigest() != native['executable_sha256']:
            raise DumpError('IPA executable hash mismatch')
        if not archive.getinfo(prefix+info['CFBundleExecutable']).external_attr >> 16 & 0o111:
            raise DumpError('IPA executable lost its executable permissions')
        for name, dump, key in [('base.cci',base,'game_sha256'),('update.cia',update,'update_sha256')]:
            member = prefix+'Game/'+name
            if dump is None:
                if member in names: raise DumpError('Unexpected update in IPA')
                continue
            if archive.getinfo(member).file_size != dump.path.stat().st_size:
                raise DumpError('IPA game size mismatch')
            digest = hashlib.sha256()
            with archive.open(member) as data:
                for chunk in iter(lambda:data.read(8*1024*1024),b''): digest.update(chunk)
            if digest.hexdigest() != manifest[key]:
                raise DumpError('IPA game hash mismatch')
        for scale in (2,3):
            if prefix+f'AppIcon60x60@{scale}x.png' not in names:
                raise DumpError(f'IPA missing {60*scale}-pixel app icon')
        return {'verified':True,'build':info['CFBundleVersion'],'sdk':native['sdk'],
                'shell_commit':native['shell_source_commit'],'bytes':path.stat().st_size}


def select_update(base: Dump, updates: list[Dump]) -> Dump | None:
    candidates = [u for u in updates if u.title_id == base.title_id | 0x0000000E00000000]
    if not candidates:
        return None
    newest = max(u.title_version or 0 for u in candidates)
    valid = [u for u in candidates if (u.title_version or 0) == newest and u.decrypted]
    if len(valid) != 1:
        raise DumpError(f'{base.title_id:016X}: need exactly one decrypted update at newest version {newest}')
    return valid[0]


def plan(folder: Path) -> list[tuple[Dump, Dump | None]]:
    bases: dict[int,list[Dump]] = {}; updates = []
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in ('.3ds','.cci','.cia') or not path.is_file():
            continue
        dump = inspect_dump(path, require_decrypted=False)
        if dump.kind == 'cci' and dump.title_id >> 32 == 0x00040000 and dump.decrypted:
            bases.setdefault(dump.title_id,[]).append(dump)
        elif dump.kind == 'cia' and dump.title_id >> 32 == 0x0004000e:
            updates.append(dump)
    if not bases:
        raise DumpError('No decrypted cartridge backups found')
    result = []
    for title, copies in sorted(bases.items()):
        if len(copies) != 1:
            raise DumpError(f'{title:016X}: several decrypted base dumps; choose one in a separate input folder')
        result.append((copies[0],select_update(copies[0],updates)))
    return result


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder',type=Path)
    parser.add_argument('--work',type=Path,default=Path.home()/'Development/3ds-work')
    parser.add_argument('--out',type=Path)
    parser.add_argument('--prepare-only',action='store_true',help='Cache and validate inputs while the common shell builds')
    args=parser.parse_args()
    folder=args.folder.expanduser().resolve(); packer.check_nas(folder)
    work=packer.outside_repo(args.work); out=packer.outside_repo(args.out or work)
    (work/'logs').mkdir(parents=True,exist_ok=True)
    titles=plan(folder)
    report=[]
    for index,(base,update) in enumerate(titles,1):
        packer.check_nas(folder)
        name=packer.title_name(base)
        private=work/'titles'/f'{base.title_id:016X}'/'Game'; private.mkdir(parents=True,exist_ok=True)
        print(f'[{index}/{len(titles)}] Caching {name}',flush=True)
        packer.cache_input(base,private/'base.cci')
        if update: packer.cache_input(update,private/'update.cia')
        row={'name':name,'title_id':f'{base.title_id:016X}','base':str(base.path),
             'update':str(update.path) if update else None,'update_version':update.title_version if update else None}
        if not args.prepare_only:
            command=[sys.executable,str(packer.REPO/'scripts/build_ipa_from_3ds.py'),str(base.path),
                     '--work',str(work),'--out',str(out)]
            if update: command+=['--update',str(update.path)]
            log=work/'logs'/f'batch-{base.title_id:016X}.log'
            print(f'[{index}/{len(titles)}] Packing {name}; log: {log}',flush=True)
            with log.open('w') as output:
                result=subprocess.run(command,stdout=output,stderr=subprocess.STDOUT)
            row['returncode']=result.returncode
            if result.returncode == 0:
                row['ipa']=str(out/(packer.ipa_name(name)+'.ipa'))
                print(f'[{index}/{len(titles)}] Verifying archive and bundled input hashes',flush=True)
                row.update(verify_package(Path(row['ipa']),base,update))
                print(f'[{index}/{len(titles)}] Ready: {row["ipa"]}',flush=True)
            else:
                print(f'[{index}/{len(titles)}] Failed: {name}; see {log}',flush=True)
        report.append(row)
        (work/'logs'/('batch-prepared.json' if args.prepare_only else 'batch-pack-results.json')).write_text(json.dumps(report,indent=2)+'\n')
    if any(r.get('returncode',0) for r in report):
        raise SystemExit(1)


if __name__=='__main__':
    try: main()
    except (ValueError,OSError) as error:
        print(f'Error: {error}',file=sys.stderr);sys.exit(1)
