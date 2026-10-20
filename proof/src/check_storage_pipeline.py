"""실제 CV 캐시 재사용으로 새 임시 프로젝트의 Step 2/3와 이전 전후 렌더를 비교한다.

API 호출 함수는 차단한다. 원래 프로젝트와 최종 출력에는 쓰지 않는다.
"""
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import json
import shutil
import tempfile
from unittest.mock import patch


def run():
    original_home=Path(os.environ.get('COMIC_TRANSLATOR_HOME',Path.home()/'ComicTranslator'))
    temporary=Path(tempfile.mkdtemp(prefix='nct-portability-'))
    os.environ['COMIC_TRANSLATOR_HOME']=str(temporary/'data')
    import app
    import numpy as np
    import cv2
    from pipeline.pipeline_runner import run_single_image,DEFAULT_FONT
    from pipeline.render_text_overlay import apply_text_overlay
    source=original_home/'projects/20260530_144110'
    data=app.ensure_session('portability')
    records=app.copy_images_into_session([source/'input/00003.jpg'],'portability')
    record=app.image_record('portability',Path(records[0]['input_path']))
    # 저장된 CV를 복사하므로 새 유료 CV 호출이 필요 없다. 엔진도 그 호출이 발생하면 즉시 실패한다.
    Path(record['cv_json']).parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source/'work/json/00003_cv.json',record['cv_json'])
    import pipeline.pipeline_runner as engine
    original_run=engine._run_step
    def local_step(cmd,*args,**kwargs):
        if any('cloud_vision_api' in str(c) or 'gemini_ocr' in str(c) or 'openai' in str(c) for c in cmd):
            raise AssertionError('유료 분석 호출 금지')
        return original_run(cmd,*args,**kwargs)
    with patch.object(engine,'_run_step',local_step):
        ok,log=run_single_image(record['input_path'],record['work_dir'],record['final_jpg'],stop_after='pre_ocr')
    if not ok:raise AssertionError(log)
    raw=json.loads(Path(record['regions_json']).read_text())
    assert not Path(raw['image']).is_absolute()
    assert all(not Path(r['mask_ref']).is_absolute() for r in raw['regions'] if r.get('mask_ref'))
    journal=json.loads((original_home/'migration.json').read_text())
    backup=Path(journal['backup'])/'temp_output/v2_sessions/20260530_144110'
    counts=[]
    for label,base in [('before',backup),('after',source)]:
        count=apply_text_overlay(source/'work/masked/00003_masked.jpg',base/'work/json/00003_integrated.json',temporary/f'{label}.png',
            regions_json=base/'work/json/00003_regions.json',font_path=Path(DEFAULT_FONT),original_image_path=source/'input/00003.jpg',
            overlay_guidance_path=base/'work/json/00003_overlay_guidance.json' if (base/'work/json/00003_overlay_guidance.json').exists() else None)
        counts.append(count)
    before=cv2.imread(str(temporary/'before.png'));after=cv2.imread(str(temporary/'after.png'))
    assert np.array_equal(before,after)
    return {'new_project_analysis':True,'paid_calls':0,'regions':len(raw['regions']),'render_count':counts,'render_identical':True,'artifacts':str(temporary)}

if __name__=='__main__':print(json.dumps(run(),ensure_ascii=False))
