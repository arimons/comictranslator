# Comic Translator Studio

일본어 만화를 한국어로 번역하는 개인 프로젝트입니다. 말풍선과 바깥 글자를 자동으로 찾아 지우고, LLM이 문맥을 읽어 번역하며, 마지막 확인은 웹 편집기에서 사람이 합니다.

## 왜 만들었나

만화 번역은 글자를 읽는 일, 번역하는 일, 원래 글자를 지우고 새 글자를 넣는 일이 한꺼번에 얽혀 있습니다. 이 프로젝트는 그 세 가지를 따로 풀고 다시 이어 붙이는 방식으로 만들었습니다.

- **글자 위치는 자동으로 찾습니다.** 말풍선 안의 글자와 말풍선 바깥의 글자(나레이션, 효과음 주변 글자)를 각각 검출하고, 글자만 지워 빈 말풍선 마스크를 만듭니다.
- **글자 인식과 번역은 LLM에 맡깁니다.** 로컬 OCR은 만화체 글자의 인식률이 낮아서, 검출된 영역을 번호표와 함께 한 장의 시트로 묶어 LLM에 넘깁니다. LLM은 앞뒤 대사와 인물, 말투를 함께 보고 인식과 번역을 한 번에 합니다. 처음에는 Gemini 2.5 Flash로 시작했고, 지금은 모델을 바꿔 가며 쓸 수 있는 구조입니다.
- **끝까지 자동화하지 않았습니다.** 검출 파라미터를 한두 개 조정해서는 모든 페이지의 결과가 맞지 않았습니다. 그래서 파라미터를 계속 만지는 대신, 약 90%는 자동으로 판정하고 나머지 약 10%는 사람이 고치는 쪽을 택했습니다. 그 사람 몫을 빠르게 하려고 만든 것이 React + Canvas 편집기입니다.

## 처리 흐름

한 페이지는 다섯 단계를 거칩니다.

| 단계 | 하는 일 | 주요 산출물 |
|---|---|---|
| 1. 글자 검출 | Google Cloud Vision으로 페이지의 글자 위치를 얻습니다 | `json/{name}_cv.json` |
| 2. 글자 묶기 | 글자 조각을 줄과 대사 단위(TG)로 묶습니다 | `json/{name}_text_groups.json` |
| 3. 영역 검출·글자 제거 | 말풍선 경계(경계 기반)와 바깥 글자(텍스트 기반)를 찾아 R 영역으로 만들고 글자를 지웁니다 | `json/{name}_regions.json`, `masked/{name}_masked.jpg` |
| 4. 인식·번역 | 영역 시트를 LLM에 보내 인식과 번역을 받습니다 | `json/{name}_integrated.json` |
| 5. 글자 입히기 | 번역문을 영역에 맞춰 배치해 최종 이미지를 만듭니다 | `final/{name}_final.jpg` |

처리 기준은 **경계 기반**(말풍선처럼 경계가 있는 글자)과 **텍스트 기반**(경계가 없는 글자) 두 가지입니다. 단계별 상세는 [파이프라인 해설](docs/pipeline_guide.html)을 참고하세요.

## 편집기

웹 편집기(FastAPI + React + Konva)에서 아래 작업을 합니다.

- 자동 검출된 영역(R)을 선택, 제외하거나 분할선·시작점·수동 영역으로 고칩니다.
- 인식된 원문과 번역문, 화자를 고칩니다. 작품별 용어집과 인물·말투 메모가 번역에 반영됩니다.
- 글자의 위치와 스타일을 조정하고, 필요한 곳은 배경을 복원합니다.
- 페이지별 최종 이미지와 ZIP으로 내보냅니다.

단축키와 도구별 동작은 [편집기 매뉴얼](docs/editor_manual.html)에 정리하고 있습니다. 매뉴얼은 아직 보강 중입니다.

## 시작하기

### 준비물

이 프로젝트는 외부 서비스 두 가지를 직접 연결해야 동작합니다. 키는 각자 발급받아야 하고, 사용량에 따라 비용이 발생할 수 있습니다.

1. **Google Cloud Vision API 키**: Google Cloud Platform에서 프로젝트를 만들고 Cloud Vision API를 켠 뒤 API 키를 발급받습니다.
2. **LLM API 키**: 현재 표준 인식·번역 경로는 OpenAI API를 사용합니다. 키를 발급받아 넣습니다.

그 밖에 Python 3.11이 필요합니다. 인식 단계 외의 글자 제거(LaMa)는 로컬에서 실행되며, 모델 파일(약 196MB)은 처음 사용할 때 내려받습니다.

### 설치와 실행

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

저장소 루트에 `.env`를 만들고 발급받은 키를 넣습니다. 키는 Git에 올리지 않습니다.

```dotenv
CLOUD_VISION_API_KEY=your_cloud_vision_api_key
OPENAI_API_KEY=your_openai_api_key
# 선택: 작업 데이터 위치. 기본값은 ~/ComicTranslator
# COMIC_TRANSLATOR_HOME=/path/to/ComicTranslator
```

```sh
.venv/bin/python app.py --no-browser --port 7873
# 브라우저에서 http://127.0.0.1:7873 접속
```

빌드된 화면(`web/`)이 저장소에 들어 있어서 편집기만 쓸 때는 Node.js가 필요 없습니다. 글자는 `Fonts/` 폴더의 글꼴로 입힙니다.

### 명령줄로 처리하기

```sh
# 인식·번역 직전까지만 처리합니다. 캐시가 없으면 Cloud Vision이 호출됩니다.
.venv/bin/python run_pipeline.py --input /path/to/pages --output ~/ComicTranslator/cli_output \
  --work-dir ~/ComicTranslator/cli_work --pre-ocr --keep-cache

# 전체 처리입니다. Cloud Vision과 LLM 비용이 발생할 수 있습니다.
.venv/bin/python run_pipeline.py --input /path/to/pages --output ~/ComicTranslator/cli_output \
  --work-dir ~/ComicTranslator/cli_work --keep-cache
```

## 현재 상태

개발 중인 개인 프로젝트입니다. 검출 규칙과 편집기는 계속 바뀌고 있고, 위 준비물 설정은 아직 `.env` 파일을 직접 고치는 방식입니다. 화면에서 키와 모델을 설정하는 기능은 계획 중입니다.

## 더 보기

- [코드 지도](docs/CODEBASE_FILE_MAP.md): 파일별 역할과 실행 경로
- [설치 의존성](docs/DEPENDENCIES.md): 버전 제약과 모델 파일
- [저장 구조](docs/STORAGE_LAYOUT.md): 작업 데이터가 놓이는 위치
- [편집기 MCP](docs/editor_mcp.md): 외부 에이전트가 페이지를 관찰하고 검토 제안을 남기는 방법
- [운영 메모](docs/OPERATIONS.md): 상시 서버 운영과 개발 환경 동기화
- [후속 할 일](docs/EDITOR_TODO.md)
