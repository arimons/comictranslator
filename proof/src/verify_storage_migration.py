"""이전 뒤 입력·마스크·최종 출력과 원본 백업을 읽기 전용으로 대조한다."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hashlib
import os
import json
from pipeline.paths import DATA_HOME
from pipeline.manual_guidance import load_json


def run():
    journal=json.loads((DATA_HOME/'migration.json').read_text())
    backup=Path(journal['backup'])
    issues=[];preexisting=[];counts={'projects':0,'inputs':0,'masks':0,'finals_compared':0,'json_files':0}
    for base in sorted((DATA_HOME/'projects').iterdir()):
        if not (base/'project.json').exists():continue
        manifest=load_json(base/'project.json');counts['projects']+=1
        for image in manifest.get('images',[]):
            counts['inputs']+=1
            if not Path(image['input_path']).is_file():issues.append({'kind':'missing_input','project':base.name,'page':image['id']})
        for path in base.rglob('*.json'):
            counts['json_files']+=1
            text=path.read_text()
            if '/Users/' in text:issues.append({'kind':'absolute_user_path','file':str(path.relative_to(DATA_HOME))})
        for path in (base/'work/json').glob('*_regions.json'):
            data=load_json(path,{})
            for region in data.get('regions',[]) if isinstance(data,dict) else []:
                if region.get('mask_ref'):
                    counts['masks']+=1
                    if not Path(region['mask_ref']).is_file():
                        # 백업의 원래 경로도 없었는지 확인한다. 백업 내부 절대 링크도 백업 대상으로 재해석한다.
                        old_json=backup/'temp_output/v2_sessions'/base.name/'work/json'/path.name
                        old_payload=json.loads(old_json.read_text())
                        old=next((r.get('mask_ref') for r in old_payload.get('regions',[]) if r.get('region_id')==region.get('region_id')),None)
                        raw_repo=Path(journal['repo'])
                        def original_file(ref):
                            q=Path(ref)
                            for _ in range(10):
                                if q.is_relative_to(raw_repo):q=backup/q.relative_to(raw_repo)
                                if not q.is_symlink():return q.is_file()
                                q=Path(os.path.abspath(q.parent/os.readlink(q)))
                            return False
                        observation={'kind':'missing_mask','file':str(path.relative_to(DATA_HOME)),'region_id':region.get('region_id')}
                        (issues if old and original_file(old) else preexisting).append(observation)
        for path in (base/'output/final').glob('*_final.*'):
            original=backup/'temp_output/v2_sessions'/base.name/'output/final'/path.name
            if not original.is_file():issues.append({'kind':'missing_backup_final','project':base.name,'page':path.name});continue
            if hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(original.read_bytes()).digest():issues.append({'kind':'changed_final','project':base.name,'page':path.name})
            counts['finals_compared']+=1
    return {'counts':counts,'issues':issues,'preexisting_missing_masks':len(preexisting),'preexisting_files':sorted({p['file'] for p in preexisting}),'backup':str(backup)}

if __name__=='__main__':
    result=run();print(json.dumps(result,ensure_ascii=False,indent=2));sys.exit(bool(result['issues']))
