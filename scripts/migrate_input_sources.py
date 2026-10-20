#!/usr/bin/env python3
"""일반 페이지를 해시 일치 원본에 연결하고 분할 결과를 프로젝트 소유로 보존한다.

기본은 미리보기. 기존 입력 캐시와 분석·CV 캐시는 삭제하지 않는다.
"""
from pathlib import Path
import argparse
import copy
import json
import os
import shutil
import sys
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline.paths import DATA_HOME
from pipeline.manual_guidance import load_json,write_json
from pipeline.project_paths import manifest_files
from pipeline.source_library import SourceLibrary,derived,digest
from scripts.migrate_workspace import assert_stopped


def migrate(home,roots,apply=False):
    home=Path(home);lib=SourceLibrary(home)
    if apply:assert_stopped()
    reports=[]
    for metadata in manifest_files(home/'projects'):
        data=load_json(metadata);base=metadata.parent;before=copy.deepcopy(data)
        # 복수 원본 폴더일 때도 페이지별 연결을 순서대로 수행한다.
        project={'project_id':base.name,'pages':len(data.get('images',[])),'linked':0,'derived':sum(derived(p) for p in data.get('images',[])),'remaining':0}
        for root in roots:
            plan=lib.preview(data,root)
            if not plan['matched']:continue
            candidates=[Path(root).resolve()/p['relative_path'] for p in plan['pages'] if p['status']=='matched']
            common=Path(os.path.commonpath([str(p.parent) for p in candidates]))
            if common!=Path(root).resolve():plan=lib.preview(data,common)
            changes=[]
            by_id={p['id']:p for p in data['images']}
            for candidate in plan['pages']:
                if candidate['status']!='matched':continue
                item=by_id[candidate['image_id']]
                if not item.get('source_ref') or lib.target(item['source_ref'])!=(Path(plan['folder'])/candidate['relative_path']):changes.append(candidate)
            if changes:
                project['linked']+=len(changes)
                if apply:lib.apply(data,base,plan)
                else:
                    for c in changes:by_id[c['image_id']]['source_ref']={'preview':True}
        for item in data.get('images',[]):
            if derived(item):
                path=Path(item['input_path'])
                if apply and path.is_symlink():
                    if not path.is_file():raise ValueError(f'분할 입력이 없습니다: {base.name}/{item["id"]}')
                    temporary=path.with_name('.'+path.name+'.owned-input')
                    try:
                        shutil.copy2(path,temporary)
                        if digest(path)!=digest(temporary):raise ValueError('분할 입력 복사 검증 실패')
                        os.replace(temporary,path)
                    finally:temporary.unlink(missing_ok=True)
                item['input_storage']='derived'
                item.pop('source_ref',None);item.pop('source_fallback_path',None)
            elif not item.get('source_ref'):
                item.setdefault('input_storage','legacy_cache')
                project['remaining']+=1
        if apply:
            portable={**data,'images':[{k:v for k,v in p.items() if k!='source_status'} for p in data.get('images',[])]}
            if portable!=before:
                history=base/'work/source_history'/uuid4().hex;history.mkdir(parents=True)
                write_json(history/'manifest-before.json',before)
                write_json(base/'project.json',portable)
        reports.append(project)
    result={'dry_run':not apply,'projects':reports,'linked':sum(p['linked'] for p in reports),'derived':sum(p['derived'] for p in reports),'remaining':sum(p['remaining'] for p in reports),'cache_deleted':False}
    if apply:
        target=home/'migration_input_sources.json'
        # 재실행할 때 이전 기록도 보존한다.
        if target.exists():
            history=home/'backups'/('input-sources-'+uuid4().hex+'.json');history.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,history)
        write_json(target,result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',type=Path,default=DATA_HOME)
    parser.add_argument('--source-root',action='append',type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();home=args.home.expanduser().resolve()
    roots=[p.expanduser().resolve() for p in args.source_root or [home/'samples']]
    print(json.dumps(migrate(home,roots,args.apply),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
