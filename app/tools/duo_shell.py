#!/usr/bin/env python3
"""Export a public runtime, link a native Duo shell, or verify that artifact."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import zipfile

REPO = Path(__file__).resolve().parents[2]
FRAMEWORKS = ('UIKit Foundation Metal QuartzCore AVFoundation AVFAudio CoreGraphics CoreMedia CoreVideo CoreImage CoreText '
              'AudioToolbox CoreAudio CoreMotion GameController CoreHaptics CoreBluetooth VideoToolbox Security CFNetwork Network '
              'SystemConfiguration Accelerate IOSurface').split()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(8*1024*1024), b''):
            h.update(data)
    return h.hexdigest()


def source_digest() -> str:
    files = list((REPO/'app/ios/Sources').glob('*')) + list((REPO/'app/host').glob('*'))
    files += [REPO/'app/tools/build_cytrus.py', REPO/'app/tools/cytrus_patches.py', REPO/'app/tools/duo_shell.py']
    h = hashlib.sha256()
    for path in sorted(p for p in files if p.is_file()):
        h.update(str(path.relative_to(REPO)).encode()); h.update(path.read_bytes())
    return h.hexdigest()


def runtime_digest() -> str:
    # UIKit layout changes do not invalidate the expensive local runtime archive.
    files = list((REPO/'app/host').glob('*')) + [REPO/'app/tools/build_cytrus.py', REPO/'app/tools/cytrus_patches.py']
    h = hashlib.sha256()
    for path in sorted(p for p in files if p.is_file()):
        h.update(str(path.relative_to(REPO)).encode()); h.update(path.read_bytes())
    return h.hexdigest()


def archive(folder: Path, output: Path) -> None:
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for path in sorted(folder.rglob('*')):
            if path.is_symlink():
                raise ValueError(f'Symlink not permitted in public build artifact: {path.name}')
            if path.is_file():
                z.write(path, str(path.relative_to(folder)))


def unpack(path: Path, output: Path) -> None:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or z.testzip() is not None:
            raise ValueError('Corrupt or ambiguous shell archive')
        for item in z.infolist():
            p = Path(item.filename)
            if p.is_absolute() or '..' in p.parts or (item.external_attr >> 16 & 0o170000) == 0o120000:
                raise ValueError('Unsafe shell archive path')
        z.extractall(output)
        for item in z.infolist():
            p = output/item.filename
            if p.is_file():
                p.chmod((item.external_attr >> 16 & 0o777) or 0o644)


def export(work: Path, output: Path) -> None:
    if REPO == output.resolve() or REPO in output.resolve().parents:
        raise ValueError('Build artifacts must be outside the repository')
    products = work/'builds/cytrus-device/Build/Products/Release-iphoneos'
    stage = work/'native-duo-input'
    stage.mkdir(parents=True, exist_ok=True)
    # An explicit allowlist: never export the containing app or its Game folder.
    shutil.copyfile(products/'libN3DSRuntime.a', stage/'libN3DSRuntime.a')
    frameworks = products/'3dsnative.app/Frameworks'
    def writable_copy(source, destination):
        dest = Path(destination)
        if dest.exists():
            dest.chmod(dest.stat().st_mode | 0o200)
        shutil.copyfile(source, destination)
        shutil.copymode(source, destination)
        dest.chmod(dest.stat().st_mode | 0o200)
        return str(dest)
    for name in ('SDL3', 'OpenSSL', 'MoltenVK'):
        # SwiftPM's OpenSSL binary is read-only. Keep this owned export reusable
        # while retaining executable bits, without copying filesystem flags.
        shutil.copytree(frameworks/f'{name}.framework', stage/'Frameworks'/f'{name}.framework', dirs_exist_ok=True, copy_function=writable_copy)
    for path in stage.rglob('*'):
        relative = path.relative_to(stage)
        if relative.parts[0] not in ('Frameworks','libN3DSRuntime.a','runtime.json') or path.suffix.lower() in ('.cci','.3ds','.cia','.cxi','.3dsx','.keys') or 'Game' in relative.parts:
            raise ValueError(f'Unexpected file in public runtime input: {relative}')
    manifest = {'abi':1, 'source_sha256':source_digest(), 'runtime_source_sha256':runtime_digest(),
                'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
                'files':{str(p.relative_to(stage)):digest(p) for p in stage.rglob('*') if p.is_file() and p.name != 'runtime.json'}}
    (stage/'runtime.json').write_text(json.dumps(manifest,indent=2)+'\n')
    archive(stage, output)
    print(f'Public game-free input: {output} ({output.stat().st_size} bytes)')


def build(inputs: Path, output: Path, work: Path) -> None:
    sdk_version = subprocess.check_output(['xcrun','--sdk','iphoneos','--show-sdk-version'],text=True).strip()
    if tuple(map(int,sdk_version.split('.'))) < (27,1):
        raise ValueError('Native Duo shell requires the iOS 27.1 SDK or newer')
    sdk = subprocess.check_output(['xcrun','--sdk','iphoneos','--show-sdk-path'],text=True).strip()
    if not (Path(sdk)/'System/Library/Frameworks/UIKit.framework/Headers/UIHingeInteraction.h').exists():
        raise ValueError('Native hinge declarations are absent')
    stage = work/'input'; unpack(inputs, stage)
    metadata = json.loads((stage/'runtime.json').read_text())
    if metadata.get('runtime_source_sha256') != runtime_digest():
        raise ValueError('Runtime sources differ from the locally built archive')
    for relative, expected in metadata['files'].items():
        if digest(stage/relative) != expected:
            raise ValueError(f'Runtime input hash mismatch: {relative}')
    app = work/'output/3dsnative.app'; app.mkdir(parents=True,exist_ok=True)
    shutil.copytree(stage/'Frameworks',app/'Frameworks',dirs_exist_ok=True)
    objects = []
    for source in sorted((REPO/'app/ios/Sources').glob('*.m')):
        obj = work/(source.stem+'.o'); objects.append(str(obj))
        subprocess.run(['xcrun','clang','-target','arm64-apple-ios26.0','-isysroot',sdk,'-fobjc-arc','-fmodules','-O2',
                        '-DN3DS_REQUIRE_NATIVE_DUO=1','-I',str(REPO/'app/host'),'-c',str(source),'-o',str(obj)],check=True)
    link = ['xcrun','clang++','-target','arm64-apple-ios26.0','-isysroot',sdk,*objects,str(stage/'libN3DSRuntime.a'),
            '-F',str(stage/'Frameworks'),'-Wl,-rpath,@executable_path/Frameworks','-Wl,-no_adhoc_codesign','-lz','-liconv','-o',str(app/'3dsnative')]
    for name in FRAMEWORKS+['SDL3','OpenSSL','MoltenVK']:
        link += ['-framework',name]
    subprocess.run(link,check=True)
    info = {'CFBundleExecutable':'3dsnative','CFBundleIdentifier':'org.threedsnative.shell', 'CFBundleName':'3dsnative',
            'CFBundlePackageType':'APPL','CFBundleVersion':'2','CFBundleShortVersionString':'0.2','MinimumOSVersion':'26.0',
            'DTSDKName':'iphoneos'+sdk_version,'DTPlatformName':'iphoneos','UIDeviceFamily':[1],'UILaunchScreen':{},
            'UIFileSharingEnabled':True,'LSSupportsOpeningDocumentsInPlace':True,
            'UIApplicationSceneManifest':{'UIApplicationSupportsMultipleScenes':False,'UISceneConfigurations':{'UIWindowSceneSessionRoleApplication':[
                {'UISceneConfigurationName':'Game','UISceneDelegateClassName':'N3DSSceneDelegate'}]}},
            'UISupportedInterfaceOrientations':['UIInterfaceOrientationPortrait','UIInterfaceOrientationPortraitUpsideDown','UIInterfaceOrientationLandscapeLeft','UIInterfaceOrientationLandscapeRight'],
            'NSCameraUsageDescription':'Use the camera for supported game features.',
            'NSMicrophoneUsageDescription':'Use the microphone for supported game features.'}
    (app/'Info.plist').write_bytes(plistlib.dumps(info))
    metadata.update(sdk=sdk_version, source_sha256=source_digest(),
                    shell_source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
                    executable_sha256=digest(app/'3dsnative'))
    (app/'native-duo.json').write_text(json.dumps(metadata,indent=2)+'\n')
    verify(app)
    archive(app.parent,output)
    print(f'Native game-free Duo shell: {output}')


def verify(app: Path) -> dict:
    info = plistlib.loads((app/'Info.plist').read_bytes())
    metadata = json.loads((app/'native-duo.json').read_text())
    if metadata['abi'] != 1 or metadata['source_sha256'] != source_digest():
        raise ValueError('Stale or incompatible native Duo shell')
    binary = app/info['CFBundleExecutable']
    if digest(binary) != metadata['executable_sha256']:
        raise ValueError('Native executable hash mismatch')
    load = subprocess.check_output(['xcrun','vtool','-show-build',str(binary)],text=True)
    actual_sdk = next(line.split()[1] for line in load.splitlines() if line.strip().startswith('sdk '))
    if tuple(map(int,actual_sdk.split('.'))) < (27,1) or actual_sdk != metadata['sdk']:
        raise ValueError('The final executable was not linked with the native Duo SDK')
    strings = binary.read_bytes()
    for symbol in (b'UIHingeInteraction',b'reservedRegionsOfKind:options:',b'hingeChanged:',b'N3DSSceneDelegate'):
        if symbol not in strings:
            raise ValueError(f'Native Duo path missing: {symbol.decode()}')
    if info.get('UIRequiresFullScreen') or not info.get('UIApplicationSceneManifest'):
        raise ValueError('Legacy window policy in native Duo shell')
    if (app/'Game').exists():
        raise ValueError('Reusable shell unexpectedly contains private game files')
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['export','build','verify'])
    parser.add_argument('--work',type=Path,default=Path.home()/'Development/3ds-work')
    parser.add_argument('--input',type=Path)
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    work = args.work.expanduser().resolve(); work.mkdir(parents=True,exist_ok=True)
    if args.command == 'export':
        export(work,args.output or work/'native-duo-runtime.zip')
    elif args.command == 'build':
        if args.input is None or args.output is None:
            parser.error('build requires --input and --output')
        build(args.input,args.output,work)
    else:
        print(json.dumps(verify(args.input or work/'native-duo-shell/3dsnative.app'),indent=2))


if __name__ == '__main__': main()
