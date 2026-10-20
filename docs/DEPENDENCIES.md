# Python 의존성과 모델 파일

2026-10-21 정리. 현재 실행 경로의 import와 설치 메타데이터를 대조했다. 동작 중인 버전을 고정했고 API 모델·번역 정책은 변경하지 않았다.
Python 3.11 / macOS Apple Silicon 환경에서 검증한다. 다른 플랫폼은 requirements.txt로 설치하고 해당 환경에서 검증한다.

| 파일 | 용도 |
|---|---|
| requirements.txt | FastAPI 편집기, MCP, REST Cloud Vision, OpenAI, CV·렌더, LaMa |
| requirements-dev.txt | 기본 설치 + pytest·HTTP 테스트 |
| batch/requirements.txt | 기본 설치 + 선택 Google Batch SDK |
| requirements-lock-macos-arm64-py311.txt | 정리한 실제 환경의 전체 설치 버전. 개발·선택 배치 포함 |

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# 개발 검증을 하는 기기
.venv/bin/python -m pip install -r requirements-dev.txt
# 별도 Google Batch 도구를 사용하는 경우에만
.venv/bin/python -m pip install -r batch/requirements.txt
.venv/bin/python -m pip check
```

기존 requirements의 google-cloud-vision은 실제 코드가 REST requests 방식이어서 제거했다.
메인 OCR·번역·이미지 편집은 OpenAI SDK를 사용한다. Google SDK는 표준 파이프라인에 필요하지 않다.
선택 배치 파일 업로드는 google-generativeai에서 google-genai의 client.files.upload로 변경하여 구형 SDK를 제거했다.
Gradio, pandas, Hugging Face 도구 및 그 전용 의존성도 현재 실행 그래프에 없어 로컬 .venv에서 제거했다.
직접 의존성 목록을 무차별 pip freeze 결과로 덮지 않고, 전체 freeze는 별도 플랫폼 잠금 파일로 관리한다.

## LaMa

LaMa를 유지하므로 torch, torchvision, simple-lama-inpainting은 필수다. 검증한 조합을 requirements에 명시한다.
wrapper 0.1.2가 Pillow<10 및 NumPy<2를 요구하여 해당 제한을 유지한다.
제약 근거: https://github.com/enesmsahin/simple-lama-inpainting/blob/main/pyproject.toml

모델은 패키지와 별개인 약 196MB의 big-lama.pt다. 기본 위치:

`~/ComicTranslator/cache/models/torch/hub/checkpoints/big-lama.pt`

기존 ~/.cache/torch의 모델은 위 위치로 복사하고 SHA-256 동일성을 확인했다. 기존 사본은 삭제하지 않았다.
첫 사용 시 pipeline/paths.py의 configure_model_cache가 TORCH_HOME을 데이터 홈으로 설정한다.
위 파일이 있으면 LAMA_MODEL로 사용하며, 사용자가 명시한 TORCH_HOME/LAMA_MODEL은 덮어쓰지 않는다.
모델이 없는 기기에서는 처음 사용할 때 wrapper가 다운로드한다. 맥 미니에 모델 파일도 복사하면 다운로드가 필요 없다.
이 모델 캐시는 cache/cv 및 cache/analysis와 별개다. 입력 cache/input은 프로젝트 원본 연결을 유지하므로 함부로 비우지 않는다.

LAMA_DEVICE는 자동 선택(cuda → mps → cpu)을 유지한다. GPU 초기화 실패 시 CPU로 전환하는 기존 동작도 유지한다.
시각적 복원 품질을 자동 판정하지 않는다. 이번 검증은 파일 동일성·모델 로딩·출력 크기·테스트 통과 여부만 확인한다.

## 인증

현재 Cloud Vision REST는 CLOUD_VISION_API_KEY, OpenAI는 OPENAI_API_KEY를 .env에서 읽는다.
선택 Google Batch에는 GEMINI_API_KEY가 필요하다. 값을 저장소나 로그에 출력하지 않는다.
google-credentials.json은 과거 구성 보존 파일이며 현재 REST 경로의 필수 입력은 아니다.
새 기기에서는 기존 .env의 경로 설정과 사용자 폰트가 유효한지 확인한다.

## 폴더 ID와 표시 이름

projects 아래 폴더 ID는 기존 timestamp ID를 유지한다. project.json의 project_name은 바뀔 수 있는 표시 이름이다.
작품명 변경 때문에 폴더명·다른 프로젝트 연결을 바꾸지 않는다. 기존 자동분할 프로젝트의 접미 ID도 그대로 유지한다.
