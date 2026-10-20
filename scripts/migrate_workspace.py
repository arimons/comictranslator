#!/usr/bin/env python3
"""로컬 작업 데이터를 데이터 홈으로 이전한다. 기본값은 읽기 전용 미리보기.

백업은 APFS clone 복사로 만들고 검증한다. 기존 파일을 삭제하지 않으며,
충돌 시 중단한다. rollback은 현재 이전본도 별도 보존한 뒤 백업을 복원한다.
"""
from __future__ import annotations
import argparse
from collections import defaultdict, Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.paths import REPO_ROOT, DATA_HOME
from pipeline.project_paths import PATH_KEYS, storage_json


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".migration-tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def files_under(path):
    if path.is_symlink() or path.is_file():
        yield path
    elif path.is_dir():
        # os.walk는 symlink 디렉터리를 따라가지 않는다.
        for current, dirs, files in os.walk(path):
            current = Path(current)
            for name in dirs:
                if (current / name).is_symlink():
                    yield current / name
            for name in files:
                yield current / name


def inventory(path):
    return {str(p.relative_to(path)): ("link", os.readlink(p)) if p.is_symlink()
            else ("file", p.stat().st_size) for p in files_under(path)}


def plan(repo, home):
    old = repo / "temp_output"
    moves = []
    groups = defaultdict(list)
    projects = []
    sessions = old / "v2_sessions"
    for directory in sorted(sessions.iterdir()) if sessions.exists() else []:
        if not directory.is_dir():
            continue
        metadata = directory / "session.json"
        if not metadata.exists():
            projects.append((directory, False)); continue
        data = read(metadata)
        images = data.get("images", [])
        hashes = tuple(p.get("image_sha256") for p in images)
        has_work = any((directory / "output/final").glob("*_final.*")) or any((directory / "work/json").glob("*_manual_guidance.json"))
        duplicate_candidate = not data.get("project_name") and len(images) == 171 and all(hashes) and not has_work
        if duplicate_candidate:
            groups[hashes].append(directory)
        projects.append((directory, duplicate_candidate))
    duplicates = {p for values in groups.values() if len(values) > 1 for p in values}
    for directory, _ in projects:
        destination = home / ("legacy/projects_test" if directory in duplicates else "projects") / directory.name
        moves.append((directory, destination))
    names = {"v2_input_cache": "cache/input", "v2_cv_cache": "cache/cv", "v2_analysis_cache": "cache/analysis",
             "v2_glossaries": "glossaries", "image_generation_inbox": "inbox/image_generation"}
    for name, target in names.items():
        if (old / name).exists(): moves.append((old / name, home / target))
    for path in sorted(old.iterdir()) if old.exists() else []:
        if path.name == "v2_sessions" or path.name in names: continue
        target = "legacy/temp_output/debug" if path.name.startswith("debug_") else "legacy/temp_output"
        moves.append((path, home / target / path.name))
    for name, target in {"output": "legacy/output", "test_image": "samples/test_image", "test_image1": "samples/test_image1"}.items():
        if (repo / name).exists(): moves.append((repo / name, home / target))
    proof = repo / "proof"
    for path in sorted(proof.iterdir()) if proof.exists() else []:
        if path.name == "src": continue
        # 강제 추적된 출력물이 있으면 임의 이동하지 않는다.
        tracked = subprocess.run(["git", "ls-files", "--", str(path.relative_to(repo))], cwd=repo,
                                 capture_output=True, text=True, check=True).stdout.strip()
        if tracked: raise RuntimeError(f"proof 출력에 추적 파일이 있습니다: {path.name}")
        moves.append((path, home / "legacy/proof_outputs" / path.name))
    return moves


def relocate(path, moves):
    path = Path(os.path.abspath(path))
    for old, new in sorted(moves, key=lambda item: -len(str(item[0]))):
        if path == old or path.is_relative_to(old): return new / path.relative_to(old)
    return path


def preview(repo, home, moves):
    counts = Counter(); fields = Counter(); total = 0
    for old, _ in moves:
        for f in files_under(old):
            if f.is_symlink(): counts['links'] += 1; continue
            total += f.stat().st_size; counts['files'] += 1
            if f.suffix == '.json':
                counts['json_files'] += 1
                try: data = read(f)
                except (ValueError, UnicodeError): continue
                def walk(v, k=''):
                    if isinstance(v,dict):
                        for a,b in v.items(): walk(b,a)
                    elif isinstance(v,list):
                        for b in v: walk(b,k)
                    elif isinstance(v,str) and (v.startswith(str(repo)) or (k in PATH_KEYS and Path(v).is_absolute())): fields[k] += 1
                walk(data)
    return {'dry_run': True, 'repo': str(repo), 'home': str(home), 'moves': [[str(a),str(b)] for a,b in moves],
            'counts':dict(counts), 'json_path_fields':dict(fields), 'bytes':total,
            'preserved_unnamed_with_outputs': '20260530_100023'}


def rewrite_value(value, original, destination, moves, repo):
    def walk(v,k=''):
        if isinstance(v,dict): return {a:walk(b,a) for a,b in v.items()}
        if isinstance(v,list): return [walk(b,k) for b in v]
        if isinstance(v,str) and (k in PATH_KEYS or k in {'patch_png','mask_png'}):
            if Path(v).is_absolute():
                p=relocate(v,moves)
            elif v.startswith('temp_output/'):
                p=relocate(repo/v,moves)
            else: return v
            if k in {'patch_png','mask_png'}: return os.path.relpath(p,destination.parent)
            return str(p)
        return v
    return storage_json(destination,walk(value))


def assert_stopped():
    with socket.socket() as s:
        if s.connect_ex(('127.0.0.1',7873)) == 0:
            raise RuntimeError('7873 편집기 서버를 먼저 종료하세요')


def clone(source, destination):
    if destination.exists(): raise RuntimeError(f'백업 경로 충돌: {destination}')
    destination.parent.mkdir(parents=True,exist_ok=True)
    # -c는 clonefile을 강제한다. 지원되지 않으면 일반 복사로 조용히 대체하지 않는다.
    subprocess.run(['/bin/cp','-cRp',str(source),str(destination)],check=True)
    if inventory(source) != inventory(destination): raise RuntimeError('백업 파일·링크 목록 검증 실패')


def apply(repo, home, moves):
    assert_stopped()
    if any(dst.exists() or dst.is_symlink() for _,dst in moves): raise RuntimeError('목적지 충돌: 기존 데이터에 덮어쓰지 않습니다')
    if home.exists() and home.stat().st_dev != repo.stat().st_dev: raise RuntimeError('같은 APFS 볼륨에서만 자동 이전합니다. 백업 위치를 지정하는 별도 검토가 필요합니다')
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    backup=home/'backups'/f'pre-migration-{stamp}'
    roots=[repo/name for name in ['temp_output','proof','output','test_image','test_image1'] if (repo/name).exists()]
    for source in roots:
        clone(source,backup/source.name)
    manifest={'version':1,'repo':str(repo),'home':str(home),'backup':str(backup),'status':'backed_up',
              'roots':[str(p) for p in roots], 'moves':[[str(a),str(b)] for a,b in moves], 'completed_moves':[]}
    journal=home/'migration.json'
    if journal.exists(): raise RuntimeError('기존 migration.json이 있습니다. 덮어쓰지 않습니다')
    write(journal,manifest)
    json_files=[];links=[]
    for old,new in moves:
        for f in files_under(old):
            mapped=new/f.relative_to(old) if f != old else new
            if f.is_symlink():
                target=Path(os.path.abspath(f.parent/os.readlink(f)))
                links.append((mapped,relocate(target,moves)))
            elif f.suffix=='.json': json_files.append((f,mapped))
    manifest['status']='moving';write(journal,manifest)
    for old,new in moves:
        new.parent.mkdir(parents=True,exist_ok=True);old.rename(new)
        manifest['completed_moves'].append([str(old),str(new)]);write(journal,manifest)
    # 입력 및 분석 캐시 링크를 새 위치 기준 상대 링크로 복구한다.
    for link,target in links:
        temporary=link.with_name(link.name+'.migration-link')
        temporary.symlink_to(os.path.relpath(target,link.parent));os.replace(temporary,link)
    changed=0
    manifest['status']='rewriting';write(journal,manifest)
    for original,destination in json_files:
        try:data=read(destination)
        except (ValueError,UnicodeError):continue
        updated=rewrite_value(data,original,destination,moves,repo)
        if destination.name=='session.json' and destination.parent.parent.name in {'projects','projects_test'}:
            target=destination.with_name('project.json')
            if target.exists(): raise RuntimeError('project.json 충돌')
            destination.rename(target);destination=target
        if data!=updated:write(destination,updated);changed+=1
    config=home/'config.json'
    if not config.exists():write(config,{'library_roots':[],'exports_dir':'exports'})
    for path in ['projects','cache/input','cache/cv','cache/analysis','glossaries','inbox/image_generation','exports','samples','legacy']:
        (home/path).mkdir(parents=True,exist_ok=True)
    broken=[str(link) for link,_ in links if not link.exists()]
    manifest.update(status='complete',json_rewritten=changed,links_rewritten=len(links),broken_links=broken)
    write(journal,manifest)
    return manifest


def rollback(journal):
    record=read(journal);repo=Path(record['repo']);home=Path(record['home']);backup=Path(record['backup'])
    assert_stopped()
    keep=home/'legacy'/('rollback-'+datetime.now().strftime('%Y%m%d_%H%M%S'))
    for index,(_,target) in enumerate(record['completed_moves']):
        target=Path(target)
        if target.exists() or target.is_symlink():
            dst=keep/str(index)/target.name;dst.parent.mkdir(parents=True,exist_ok=True);target.rename(dst)
    for source in map(Path,record['roots']):
        if source.exists():
            dst=keep/'original-location'/source.name;dst.parent.mkdir(parents=True,exist_ok=True);source.rename(dst)
        clone(backup/source.name,source)
    record['status']='rolled_back';record['preserved_current']=str(keep);write(journal,record)
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=REPO_ROOT)
    parser.add_argument('--home',type=Path,default=DATA_HOME)
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--rollback',action='store_true')
    args=parser.parse_args();repo=args.repo.expanduser().absolute();home=args.home.expanduser().absolute()
    if args.rollback:
        if not args.apply:parser.error('--rollback은 --apply를 함께 지정해야 합니다')
        result=rollback(home/'migration.json')
    else:
        moves=plan(repo,home)
        result=apply(repo,home,moves) if args.apply and not args.dry_run else preview(repo,home,moves)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
