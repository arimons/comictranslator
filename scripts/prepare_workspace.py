#!/usr/bin/env python3
"""데이터 홈의 기본 폴더와 읽기용 안내를 생성한다. 기존 프로젝트·안내는 덮어쓰지 않는다."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pipeline.paths import DATA_HOME,MODEL_CACHE_DIR


def prepare():
    for relative in ['projects','cache/input','cache/cv','cache/analysis','glossaries',
                     'inbox/image_generation','exports','samples','legacy','backups']:
        (DATA_HOME/relative).mkdir(parents=True,exist_ok=True)
    MODEL_CACHE_DIR.mkdir(parents=True,exist_ok=True)
    index=DATA_HOME/'README.md'
    if not index.exists():
        index.write_text('''# Comic Translator 작업 데이터

- projects/: 프로젝트 ID별 작업물. 폴더 ID는 유지하고 작품명은 project.json의 project_name으로 바꿉니다.
- cache/input/: 이전 프로젝트의 호환·대체 사본. 새 일반 페이지는 원본 폴더를 직접 참조합니다.
- cache/cv/, cache/analysis/: API/CV 및 분석 캐시.
- cache/models/: LaMa 등 모델 파일.
- local/hosts/: 기기별 원본 폴더 경로.
- 프로젝트 input/: 분할·크롭은 실제 파일, 일반 원본은 앱이 관리하는 입력 별칭입니다.
- glossaries/: 작품 메모·용어집.
- exports/: 내보낸 결과.
- inbox/image_generation/: 생성 결과 수신.
- samples/: 테스트 이미지.
- legacy/: 이전 결과·중복 프로젝트·검증 산출물 보관.
- backups/: 이전 전 데이터와 공개 전 원본 Git 이력. 자동 삭제하지 않습니다.

코드와 데이터는 별개입니다. Python 패키지는 코드 폴더의 .venv에 설치합니다.
맥 미니에는 projects + glossaries를 옮기고 앱의 원본 연결에서 이미 보유한 원본 폴더를 지정하세요.
cache/input은 재연결 전 대체 입력이며 이전 보관 프로젝트도 사용하므로 아직 삭제하지 마세요.
cache/models의 big-lama.pt도 가져가면 다시 다운로드할 필요가 없습니다.
이 데이터 홈은 git에 포함되지 않으니 필요한 파일만 직접 복사하세요.
''',encoding='utf-8')
    return DATA_HOME

if __name__=='__main__':print(prepare())
