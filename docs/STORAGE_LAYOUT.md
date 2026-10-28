# 저장소와 작업 데이터

2026-10-20 저장소 정리. 실행점은 `app.py`, 프론트 빌드 출력은 `web/`, 보조 실행기는 `scripts/`다.
`pipeline/paths.py`가 저장 위치의 유일한 정의다. `session_id` 및 API 이름은 호환을 위해 유지한다.

## 저장 위치

기본 데이터 홈은 `~/ComicTranslator`이며 `COMIC_TRANSLATOR_HOME` 환경변수 또는 코드 폴더의 `.env`로 바꾼다.

| 위치 | 보존 기준 |
|---|---|
| `projects/{id}/` | 사용자 작업물. input, work, output 포함하여 백업. 화자 지정·R별 번역 메모는 `work/speakers/` |
| `cache/input/` | 이전 프로젝트의 호환·대체 입력 사본. 신규 일반 페이지는 직접 참조 |
| `cache/cv/`, `cache/analysis/` | 재생성 가능한 분석 결과. 재생성 시 API 비용 발생 가능 |
| `glossaries/` | 작품 메모·용어집. 백업 |
| `inbox/image_generation/` | 외부 생성 결과 수신 |
| `exports/` | 내보낸 zip. config.json의 exports_dir로 위치 변경 가능 |
| `samples/` | 테스트 이미지. 코드에 포함하지 않음 |
| `legacy/` | 이전 출력과 검증 산출물. 원본을 삭제하지 않고 이전 |
| `backups/` | 이전 전 APFS 복제 백업. 삭제 결정은 사용자에게 맡김 |

일반 페이지는 원본 라이브러리를 직접 참조하고 분할·크롭은 프로젝트에 보관합니다. 가로 이미지 나누기가 만든 조각도 같은 프로젝트의 `input/`에 PNG로 보관합니다(별도 프로젝트를 만들지 않습니다).
이전 cache/input은 원본 미연결 시 대체 입력으로 남깁니다. 원본 연결 완료를 확인하기 전에는 삭제하지 마세요.
맥 미니에는 projects와 glossaries를 옮기고 **원본 연결**에서 이미 보유한 원본 폴더를 지정합니다.
기존 입력 사본과 CV·분석 캐시도 함께 옮기면 재연결 전 작업 및 캐시 재사용이 가능합니다.
자세한 이전 방법과 브라우저 업로드 예외는 [원본 참조 안내](INPUT_SOURCES.md)를 참고합니다.

배경 복원 데이터는 `projects/{id}/work/restore/{페이지}/`에 둡니다. `state.json`(확정 패치 목록), `patches/`(확정 패치 PNG),
`preview.png`(확정 패치를 얹은 미리보기, 영역 분석 탭 "마스크"가 사용), `jobs/{작업 ID}/`(실행 후보)로 나뉩니다.
AI 글자 지우기 작업은 `jobs/` 안에 보낸 이미지(OpenAI는 `ai_erase_request.png`, Gemini는 `gemini_image_request.png`)와 후보(`crop_after.png`)를 남깁니다. 확정 패치의 `usage`에는 토큰 수와 추정 비용(실제 청구액이 아님)이 남습니다.
원본 라이브러리 등록은 `local/hosts/{호스트 ID 해시}/source_roots.json`이며, macOS 호스트 ID는 하드웨어 UUID입니다.

## JSON 호환

새 manifest는 `project.json`이다. 기존 `session.json`도 읽으며 둘 다 있으면 project.json이 우선이다.
프로젝트 JSON의 파일 경로는 프로젝트 폴더 기준 상대 경로다. `pipeline/project_paths.py`가 저장/읽기 경계에서
변환하므로 실행 중 API 레코드는 절대 경로로 사용한다. 기존 절대 경로 JSON도 읽는다.
폰트 등 저장소 자원은 `@repo/Fonts/...`로 저장한다. 배경 복원의 patch_png와 mask_png는 원래대로 state.json 기준이다.
생성 이미지 후보는 원본·확정 출력과 별도로 projects/{id}/work/image_generation에 보관한다.

## 이전 및 복구

서버를 종료하고 다음 순서로 실행한다. 기본 실행과 --dry-run은 파일을 변경하지 않는다.

```sh
.venv/bin/python scripts/migrate_workspace.py --dry-run
.venv/bin/python scripts/migrate_workspace.py --apply
# 문제가 생겼다면 서버를 종료한 뒤 복구. 현재 이전본도 legacy 아래 보존한다.
.venv/bin/python scripts/migrate_workspace.py --rollback --apply
```

--apply는 같은 볼륨의 APFS clone 백업과 파일/링크 목록 확인을 완료해야 이동한다. 지원되지 않으면 중단한다.
목적지가 있으면 덮어쓰지 않는다. `migration.json`에 단계·완료된 이동·백업 위치를 기록한다.
이름 없는 171장 반복 프로젝트 중 최종 출력이 없는 중복을 legacy 대상으로 삼는다. 수동 가이드도 함께 보존한다.
출력이 있는 무명 프로젝트 `20260530_100023`은 이름을 바꾸거나 삭제하지 않고 보존한다.
`proof/src/`의 소스는 저장소에 남고, 나머지 proof 출력은 legacy/proof_outputs로 옮긴다.
`archive/` 자체 정리는 이번 범위에서 제외한다.

## 맥 미니 실행

1. 코드 저장소를 clone하고 `.venv`에 requirements.txt를 설치한다. frontend 빌드는 frontend/package.json 참고.
2. 데이터 홈 전체를 맥 미니의 ~/ComicTranslator로 복사한다. 다른 위치이면 COMIC_TRANSLATOR_HOME을 지정한다.
3. `.env`, `google-credentials.json`, 사용자 폰트는 별도로 복사한다. 공개 Git에는 넣지 않는다.
4. `.venv/bin/python app.py --no-browser --host 127.0.0.1 --port 7873`로 실행한다.
5. MCP는 `.venv/bin/python scripts/configure_editor_mcp.py`로 해당 머신의 경로를 등록한다.

현재 서버는 인증 없는 로컬 도구다. 원격 접속은 SSH 포트 전달을 이용하고 인터넷에 포트를 직접 공개하지 않는다.
기존 CLI의 --output은 명시적 사용자 경로를 계속 받는다. CLI 작업 중간값은 데이터 홈의 cli_work에 둔다.

이전 검증: 15개 활성 프로젝트와 출력 없는 중복 13개를 legacy에 보존. 최종 439장 해시 동일, 유료 호출 없이 신규 프로젝트 Step 2/3 및 이전 전후 렌더 픽셀 동일 확인. 기존 마스크 참조 누락은 별도 작업 기록에 남긴다. 임시 JSON 복사도 프로젝트 경계에서 상대 경로를 적용하며, 캐시의 동일성은 실행 경로를 해석한 값으로 비교한다.

Syncthing 현황(2026-10-21 MacBook 설정 확인): Developer만 공유하며 기존 temp_output/output/proof/archive/test_image1과 .venv/.git은 제외됩니다. 새 데이터 홈은 공유 범위 밖입니다. 코드 동기화와 별개로 데이터 홈을 직접 복사하거나 별도 공유해야 합니다. 실제 맥 미니 수신 여부는 여기서 확인하지 않았습니다.

2026-10-21: 기본·개발·선택 배치 의존성을 분리했습니다. LaMa 모델은 데이터 홈 cache/models에 두며 기존 Timestamp 프로젝트 ID와 가변 표시 이름을 구분합니다. [의존성 안내](DEPENDENCIES.md) 참고.

<!-- 2026-10-22 app.py 변경 검토: 페이지 가이드 파일에 inpaint_engine_overrides 키가 추가됐을 뿐 저장 위치·구조는 그대로라 이 문서 내용 영향 없음 -->

<!-- 2026-10-28 app.py(분할 조각의 원본 CV 재사용)·dialogue_extractor.py(TG 없는 영역 분할선) 변경 검토: 이 문서 내용 영향 없음 -->

<!-- 2026-10-28 app.py(영역별 번역 메모 API) 변경 검토: 이 문서 내용 영향 없음 -->

<!-- 변경 검토(2026-10-28): 텍스트 영역 크기 조절 가이드 재사용 규칙 추가, 이 문서 내용 영향 없음 -->
