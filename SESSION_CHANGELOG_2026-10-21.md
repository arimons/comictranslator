# 클라우드 세션 변경 기록 (2026-10-21)

로컬 작업과 합치기 전에 판단할 수 있도록, 이 세션에서 클론(`7e847cd`, `main`) 이후 바꾼 내용만 적는다.
로컬에 커밋하지 않은 작업이 따로 있을 수 있으므로, 합칠 때 같은 파일끼리 겹치는지 먼저 본다.

## 브랜치와 커밋

`main(7e847cd)` 위에 커밋 5개가 쌓여 있다. 두 브랜치는 이어져 있다.

| 브랜치 | 커밋 | 내용 |
|---|---|---|
| `docs/readme-project-intro` | `d34823f` | README를 프로젝트 소개 중심으로 개편하고 운영 메모를 분리 |
| | `7f588d0` | README의 OCR 전환 배경과 Cloud Vision 역할 서술 수정 |
| `feat/gemini-ocr-provider` (위 2개 포함) | `002335b` | Step 4 OCR·번역에 Gemini 모델 선택지 추가 |
| | `1be3f56` | Gemini 이미지 해상도를 medium으로 낮추고 README에 작품 메모 링크 추가 |
| | `13b9d02` | 파이프라인 해설과 코드 지도에 Gemini 모델 선택 반영 |

`main...feat/gemini-ocr-provider` 변경 파일은 10개(+307, -163)다. PR은 만들지 않았다.

## 1. README 개편 (문서만, 코드 영향 없음)

- `README.md`: 운영 메모 중심에서 프로젝트 소개 중심으로 다시 썼다.
  - 왜 만들었나(MangaOCR·PaddleOCR·EasyOCR이 만족스럽지 않아 비전 LLM으로 전환, 90% 자동 + 10% 사람 개입 편집기)
  - Step 1~5 처리 흐름(Cloud Vision은 글자 상자 좌표 용도로 명시)
  - 준비물(Cloud Vision 키, LLM 키), 설치, 실행, CLI
  - 현재 상태, 더 보기(작품 메모 링크 포함)
- `docs/OPERATIONS.md` (신규): README에서 뺀 맥 미니·PM2·Syncthing·데이터 홈 운영 내용.
- `docs/DOC_SOURCES.json`: 신규 문서를 `canonical`로 등록(저장소 테스트가 미등록 문서를 막는다).

## 2. Step 4 Gemini 모델 선택지 (코드 변경)

`pipeline/gemini_ocr_translate_v5.py`

- `SUPPORTED_OCR_MODELS`를 4개로 정리했고 항목에 `provider`를 추가했다.
  - `gpt-6.1-sol`(기본), `gpt-6-luna`, `gemini-3.8-flash`, `gemini-3.5-flash-lite`
  - Sol은 6과 6.1 가격이 같아 6.1만 둔다는 결정에 따랐다.
- `resolve_provider`: `gpt-` → openai, `gemini-` → gemini, 그 외 `ValueError`.
- `_require_api_key(provider)`: 제공자별 키 확인. `GEMINI_API_KEY`(모듈 로드 시 환경 변수에서 읽음) 추가.
- `_run_gemini_json` (신규): `google-genai`의 `generate_content`로 시트 이미지를 보낸다.
  - `response_mime_type="application/json"`, thinking level `LOW`, 최대 출력 16000 토큰
  - 이미지 해상도는 `GEMINI_MEDIA_RESOLUTION`(기본 `MEDIA_RESOLUTION_MEDIUM`)
  - 429·500·502·503·504는 OpenAI 경로와 같은 방식으로 재시도(2, 4, 8초 대기)
  - temperature는 받지만 쓰지 않는다
- `_run_model_json`: 제공자에 따라 `_run_openai_json` 또는 `_run_gemini_json`으로 분기.
- 로그·docstring의 "OpenAI OCR" 문구를 제공자 중립으로 바꿨다. 동작은 같다.
- `--model` 도움말과 argparse 설명을 갱신했다.

이 변경이 건드리지 않은 것

- OpenAI 호출 경로(`_run_openai_json`)의 동작
- 기본 모델(`gpt-6.1-sol`)과 reasoning `low`
- 용어집·작품 메모 추출(`openai_tasks.py`, `work_glossary.py`): 여전히 OpenAI 전용
- 이미지 생성 경로(`openai_image_redraw.py`, `gpt-image-2`)와 편집기 프런트엔드

모델 식별자는 웹 검색 결과로 확인했다. OpenAI 모델 페이지는 `gpt-6.1-sol`, `gpt-6-luna`, Gemini 문서는 `gemini-3.8-flash`, `gemini-3.5-flash-lite`를 정식 ID로 적고 있다. 이 세션 환경에서는 공식 페이지를 직접 열 수 없어 검색으로 대조했다. 실제 API 호출은 하지 않았다.

### 의존성과 문서

- `requirements.txt`: `google-genai==2.6.0` 추가(기존에는 `batch/requirements.txt`에만 있었음).
- `batch/requirements.txt`: 주석만 갱신. `google-genai` 줄은 그대로 둔다.
- `docs/DEPENDENCIES.md`, `docs/CODEBASE_FILE_MAP.md`, `docs/pipeline_guide.html`: Step 4 모델 선택 설명을 새 내용으로 수정. `pipeline_guide.html`은 이 저장소의 문서 최신성 테스트가 `gemini_ocr_translate_v5.py`와 묶어 검사하므로 코드를 바꾸면 함께 고쳐야 한다.

### 테스트

`tests/test_ocr_model_selection.py`: 기존의 "Gemini는 거부" 기대를 바꿨다.

- 제공자 판별(openai/gemini), 알 수 없는 모델 거부
- 지원 모델 목록과 기본값 고정
- 제공자별 분기, 제공자별 키 확인
- Gemini 요청 형태 확인(네트워크 없이 가짜 클라이언트 사용)
- Gemini 재시도 동작

## 3. 검증 결과

- 모델 선택 테스트와 의존성 테스트는 통과했다.
- 전체 테스트는 변경 전후 실패 목록이 같았다. 이 세션에서 새로 생긴 실패는 없다.
- 기존 실패(이 세션 변경과 무관): 문서 참조 검사 3건(`archive/` 등 `.gitignore` 대상 파일 참조), `test_workspace_migration` 2건, `test_render_text_overlay` 등. 폰트·`archive/`가 클론에 없는 환경 문제로 보인다.
- 실제 Gemini API 호출, 편집기 화면, 실제 만화 페이지 품질은 확인하지 않았다.

## 4. 이 세션에서 바꾸지 않았고, 확인이 필요한 것

코드를 읽고 알게 된 사실이다. 로컬 작업과 대조할 때 쓴다.

- **생성형 이미지 모델 경로는 2개라고 하셨지만 이 저장소에는 1개만 있다.**
  - 통번역(표지 등): 있다. `pipeline/openai_image_redraw.py`, `gpt-image-2`, quality `medium`, 크기는 `match_input`.
  - 말풍선·나레이션 지우기(LaMa/Telea 실패 시 생성형 모델): **코드에 없다.** `background_restore.py`의 `ENGINES`는 `("lama",)`뿐이고, `docs/TEXT_ERASE_METHODS_RESEARCH.md`에 "실패 시 gpt-image-2" 계획만 있다.
- **16 배수 처리가 둘로 나뉘어 있다.** 통번역 경로 `nearest_multiple`은 반올림(1350 → 1344), LaMa 경로 `_round_up`은 올림(1350 → 1360)이다. 말씀하신 1350x1920 → 1360x1920은 올림 쪽과 일치한다.
- **모델명 `gpt-image-2.5`로의 변경은 아직 하지 않았다.** 식별자 조회 후 확정한다고 하셨기 때문이다.
- **Gemini 이미지 생성(나노바나나) 경로는 없다.** 모델 ID와 파라미터를 조회한 뒤 설계한다.
- **Gemini 해상도를 medium으로 둔 것은 Step 4 입력 이미지(시트) 설정이다.** 이미지 생성 품질·비용과는 별개다. 품질 비교 전의 임시 선택이다.

## 5. 합칠 때 겹칠 가능성이 높은 파일

로컬에서 같은 파일을 고쳤다면 충돌이 날 수 있다.

- `README.md`, `docs/CODEBASE_FILE_MAP.md`, `docs/pipeline_guide.html`, `docs/DEPENDENCIES.md`
- `pipeline/gemini_ocr_translate_v5.py`, `requirements.txt`
- `tests/test_ocr_model_selection.py`

`docs/OPERATIONS.md`는 신규 파일이다.

## 6. 환경에 대한 메모 (저장소에는 반영되지 않음)

- 이 세션의 클론은 얕은 클론(깊이 1)이라 과거 이력을 볼 수 없다.
- 클론의 로컬 git 설정에 브랜치용 fetch 설정 두 줄을 추가했다. 푸시 상태 검사 오탐을 막기 위한 것이며 저장소 파일에는 영향이 없다.
