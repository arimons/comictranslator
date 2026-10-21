# Comic Translator Studio

일본어 만화를 한국어로 번역하고, 검출 영역·화자·번역문·글자 배치를 직접 보정하는 웹 편집기입니다.
현재 GUI는 **FastAPI + React + Konva(Canvas)**이며, 영역 분석·글자인식/번역·글자 입히기·내보내기·배경 복원 탭으로 작업합니다.

2026-10-21 현재 실행 코드 기준. [사용자 매뉴얼](docs/editor_manual.html) · [코드 지도](docs/CODEBASE_FILE_MAP.md) · [파이프라인 해설](docs/pipeline_guide.html)

## 빠른 시작

새 환경은 Python 3.11을 기준으로 설치합니다. 의존성 제약과 플랫폼별 차이는 [설치 의존성](docs/DEPENDENCIES.md)을 참고하세요.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

저장소 루트에 `.env`를 만듭니다. 실제 키와 인증 파일은 Git에 올리지 않습니다.

```dotenv
# Step 1: Google Cloud Vision REST API
CLOUD_VISION_API_KEY=your_cloud_vision_api_key
# Step 4 OCR·번역 및 작품 메모 관련 OpenAI 기능
OPENAI_API_KEY=your_openai_api_key
# 선택: 작업 데이터 위치. 기본값은 ~/ComicTranslator
# COMIC_TRANSLATOR_HOME=/path/to/ComicTranslator
```

```sh
.venv/bin/python app.py --no-browser --port 7873
# 브라우저에서 http://127.0.0.1:7873 접속
```

빌드된 프론트엔드 `web/`가 저장소에 포함돼 있어 UI 실행만 할 때 Node.js 빌드는 필요하지 않습니다.
폰트는 `Fonts/`에서 읽습니다. LaMa 패키지는 requirements에 포함되지만 모델 파일은 별도이며, 모델이 없는 환경에서는 최초 사용 시 다운로드합니다. 기존 모델 캐시를 복사해 재사용할 수 있습니다.

### Cloud Vision 인증과 JSON

**현재 구현은 `CLOUD_VISION_API_KEY`를 사용하는 REST 방식입니다.** `pipeline/cloud_vision_api.py`가 이 키를 읽고 이미지 한 장씩 요청합니다. 서비스 계정 인증 JSON과 `GOOGLE_APPLICATION_CREDENTIALS`는 현재 표준 CV 경로에서 사용하지 않습니다.

`*_cv.json`은 Cloud Vision의 **검출 결과 캐시**이고, `project.json`은 **프로젝트 상태 파일**입니다. 둘 다 인증용 JSON이 아닙니다. 이전 README의 서비스 계정 JSON·Grid CV 인증/실행 안내는 초기 구현에 대한 설명이었습니다.

## 편집 흐름

1. **프로젝트 불러오기**: 서버의 원본 폴더 경로로 불러오면 일반 페이지는 복사 없이 참조합니다. 브라우저 파일/폴더 업로드는 서버가 원본 절대 경로를 알 수 없어 프로젝트에 사본을 보관합니다.
2. **페이지 준비**: 펼침·표지 분할/크롭 미리보기를 확인하고 별도 단일 페이지 프로젝트로 만듭니다. 원본 프로젝트는 유지합니다.
3. **영역 분석**: CV 텍스트를 TG(Text Group)로 묶고 R(처리 영역)을 검출합니다. Canvas에서 TG/R 선택·제외, Edge 장벽·시작점·분할선·수동 영역으로 보정합니다.
4. **글자인식/번역**: indexed OCR sheet와 영역 정보를 이용해 OCR·번역합니다. 작품 메모의 용어·인물·말투와 R 단위 화자를 활용합니다.
5. **글자 입히기·배경 복원**: Canvas에서 번역문·글자 배치·스타일을 보정하고, 필요한 배경 복원 패치를 적용합니다.
6. **내보내기**: 페이지별 최종 이미지와 ZIP을 출력합니다. 처리 제외 페이지는 원본으로 출력하고 나중에 복원할 수 있습니다.

처리 기준은 **경계 기반 / 텍스트 기반** 두 가지입니다. Edge는 별도 타입이 아닌 경계 검출 방법이며, 화면은 R 번호와 색상으로 구분합니다. 기존 `bubble`·`unboxed` JSON 값과 캐시는 호환을 유지합니다.

삭제·실행 취소는 화면에 즉시 반영하고 무거운 재분석은 **Cmd/Ctrl+Enter**로 모아서 적용합니다. 이미지 생성 권장 페이지는 검토 후 생성 결과를 등록·선택할 수 있으며, 권장 표시만으로 자동 생성하지 않습니다.

단축키·도구별 상세 동작은 [편집기 매뉴얼](docs/editor_manual.html), 영역 용어 이력은 [변경 기록](docs/REGION_PROCESSING_BASIS_CHANGELOG.md)을 참고하세요.

## 표준 파이프라인

이미지마다 아래 다섯 단계를 subprocess로 실행합니다. Step 1은 여러 페이지를 그리드로 합쳐 한 번에 요청하는 방식이 아닙니다.

| 단계 | 실행 모듈 | 입력 → 주요 산출물 |
|---|---|---|
| 1. Cloud Vision | `pipeline/cloud_vision_api.py` | 이미지 → `json/{name}_cv.json` |
| 2. Text Grouping | `pipeline/text_group_builder_v2.py` | CV block → Text Line → TG, `json/{name}_text_groups.json` |
| 3. 영역 검출·글자 제거 | `pipeline/dialogue_extractor.py` | 경계 기반 검출 / 텍스트 기반 제거 → `json/{name}_regions.json`, `masked/{name}_masked.jpg`, `sheets/{name}_indexed_sheet.jpg` |
| 4. OCR·번역 | `pipeline/gemini_ocr_translate_v5.py` | indexed OCR sheet + regions → `json/{name}_integrated.json` |
| 5. 글자 입히기 | `pipeline/render_text_overlay.py` | 번역·영역·배치 정보 → `output_dir/final/{name}_final.jpg` |

Step 3은 FloodFill·Edge 기반 검출, 텍스트 기반 LaMa/Telea 제거, OCR sheet 구성을 담당합니다. Step 4의 파일명에 `gemini`가 남아 있지만 **현재 표준 OCR·번역은 OpenAI 경로**입니다. 모델 기본값·선택지는 코드와 편집기에서 확인합니다. Google Batch는 별도 보존 도구로, 표준 처리와 구분합니다.

배경 복원 패치는 최종 렌더에 합성합니다. 페이지 처리 제외·이미지 생성 결과 선택은 프로젝트의 페이지 정책에 저장합니다. 상세는 [파이프라인 해설](docs/pipeline_guide.html)을 참고하세요.

### CLI

```sh
# OCR·번역 직전(Step 1–3)까지만 처리. 캐시가 없으면 Cloud Vision API는 호출됩니다.
.venv/bin/python run_pipeline.py \
  --input /path/to/pages --output ~/ComicTranslator/cli_output \
  --work-dir ~/ComicTranslator/cli_work --pre-ocr --keep-cache

# 전체 처리. CV 및 OCR·번역 API 비용이 발생할 수 있습니다.
.venv/bin/python run_pipeline.py \
  --input /path/to/pages --output ~/ComicTranslator/cli_output \
  --work-dir ~/ComicTranslator/cli_work --keep-cache
```

기본 병렬 수는 4이며 `--workers`로 조정합니다. `--ocr-model`로 OCR·번역 모델을 지정할 수 있습니다. CLI의 가로 이미지 처리는 비율 기준 중앙 분할이며, 편집기의 표지 여백 판정·미리보기와 구분합니다. CLI 자동 분할을 끄려면 `--no-split-landscape`를 사용합니다.

## 코드와 작업 데이터

```text
NEW_COMIC_TRANSLATOR/         코드 저장소
├── app.py                   FastAPI API·정적 UI 서버
├── frontend/src/            React·Konva 편집기 소스
├── web/                     배포 화면·빌드된 JavaScript·CSS
├── pipeline/                CV·TG·영역·OCR/번역·렌더 엔진
├── run_pipeline.py          CLI 진입점
├── scripts/                 MCP·이전·검증 보조 실행기
├── deploy/pm2/              맥 미니 PM2 설정
├── tests/                   pytest
├── docs/                    현재 사용법·설계 문서
└── reports/                 작업 기록·과거 설명

~/ComicTranslator/           코드와 분리한 데이터 홈
├── projects/                project.json, 분할 입력, 편집 상태, 최종 출력
├── cache/                   CV·분석·LaMa 모델 및 기존 입력 호환 사본
├── glossaries/              작품 메모·용어집
├── exports/                 내보낸 파일
├── local/hosts/             호스트별 원본 폴더 연결
└── legacy/                  보존한 이전 작업물
```

일반 페이지는 원본을 읽기 전용 참조하고 분할·크롭 결과만 프로젝트가 소유합니다. 맥 미니에서 경로가 다르면 **원본 연결**에서 폴더를 지정하고 해시가 일치하는 파일만 재연결합니다. CV·분석 캐시와 기존 JSON 호환은 유지하며 `cache/input`을 일괄 삭제하지 않습니다.

[원본 참조·재연결](docs/INPUT_SOURCES.md) · [저장 구조와 이전](docs/STORAGE_LAYOUT.md) · [작품 메모](docs/WORK_GLOSSARY.md) · [이미지 생성 결과 등록](docs/IMAGE_GENERATION_REVIEW.md)

## 맥 미니 운영과 유지보수

상시 서버는 PM2의 `comictranslator`로 관리하고, Tailscale Serve HTTPS를 통해 접속합니다. [PM2 운영 가이드](deploy/pm2/README.md).

맥북의 코드 수정은 Syncthing으로 맥 미니에 동기화됩니다. Python 변경은 동기화 완료 후 맥 미니에서 `pm2 restart comictranslator`로 반영합니다. 실행 중 작업을 보호하기 위해 PM2 watch는 꺼져 있습니다.

프론트엔드를 수정했다면 먼저 빌드합니다.

```sh
npm --prefix frontend ci
npm --prefix frontend run build
# web/ 동기화 완료 후 브라우저 새로고침
```

`.venv`, `node_modules`, `.git`은 기기별로 관리합니다. 새 Python 의존성은 대상 맥의 `.venv`에도 설치해야 합니다. **데이터 홈 `~/ComicTranslator`는 현재 Syncthing 자동 동기화 대상이 아니며, 실제 운영 데이터는 맥 미니를 기준으로 사용합니다.**

## 개발·MCP·문서

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

- [코드 지도](docs/CODEBASE_FILE_MAP.md): 파일별 역할과 현재 실행 경로
- [편집기 MCP](docs/editor_mcp.md): 페이지 관찰·검토 제안과 안전한 적용 경계
- [이미지 생성 연결](docs/IMAGE_GENERATION_REVIEW.md): 권장 표시·생성 요청·후보 등록
- [후속 할 일](docs/EDITOR_TODO.md): 매뉴얼 실제 전후 예시와 UI/MCP 개선 목록
- [이전 README 보존본](reports/README_LEGACY_20251222.md): Gradio·Grid CV 등 초기 구현 기록

이전 Gradio UI와 폐기 실행기는 `archive/`에 보관돼 있으며 현재 진입점은 `app.py`입니다.
