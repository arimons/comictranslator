"""매뉴얼용 독립 프로젝트 준비/로컬 Step 3 실행. 유료 분석·공유 캐시 쓰기 없음."""
import json
import shutil
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
SID='20261020_manual_tools'
BASE=ROOT/'temp_output/v2_sessions'/SID
SOURCE=ROOT/'temp_output/v2_sessions/20261019_222000_manual'
CAP=ROOT/'proof/misc/tool_examples'

def dump(path,data):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2))

def prepare():
 from main_v2 import ensure_session,save_session
 if (BASE/'session.json').exists():
  raise SystemExit('이미 있는 검증 프로젝트는 덮어쓰지 않습니다. 새 촬영 시 SID를 새 값으로 지정하세요.')
 data=ensure_session(SID);data.update(project_name='매뉴얼 도구 전후 · 검증 복사본',images=[])
 for key,source in [('d','00006'),('t','00006'),('e','00006'),('b','00006'),('s','00008'),('u','00006')]:
  name=f'example_{key}';target=BASE/'input'/f'{name}.jpg';shutil.copy2(SOURCE/'input'/f'{source}.jpg',target)
  data['images'].append({'id':name,'name':target.name,'input_path':str(target)})
  for folder in ['json','masked','sheets','vis']:
   for path in (SOURCE/'work'/folder).glob(f'{source}_*'):
    if folder=='json' and any(x in path.name for x in ['guidance','effective','integrated','ocr_status','overlay']):continue
    dest=BASE/'work'/folder/path.name.replace(source,name,1);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
 save_session(data);CAP.mkdir(parents=True,exist_ok=True)
 print(SID)

def apply(name,path):
 from main_v2 import run_step3_with_effective_text_groups
 guidance=json.loads(Path(path).read_text())
 result=run_step3_with_effective_text_groups(SID,name,guidance)
 dump(CAP/f'{name}-result.json',{'report':result.get('guidance_report'),'regions':result.get('regions')})

if __name__=='__main__':
 if sys.argv[1]=='prepare':prepare()
 else:apply(sys.argv[2],sys.argv[3])
