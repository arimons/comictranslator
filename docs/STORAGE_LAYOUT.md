# 저장소와 작업 데이터

2026-10-20 저장소 정리. 실행점은 `app.py`, 프론트 빌드 출력은 `web/`, 보조 실행기는 `scripts/`다.
`pipeline/paths.py`가 저장 위치의 유일한 정의다. `session_id` 및 API 이름은 호환을 위해 유지한다.

## 저장 위치

기본 데이터 홈은 `~/ComicTranslator`이며 `COMIC_TRANSLATOR_HOME` 환경변수 또는 코드 폴더의 `.env`로 바꾼다.

| 위치 | 보존 기준 |
|---|---|
| `projects/{id}/` | 사용자 작업물. input, work, output 포함하여 백업 |
| `cache/input/` | 입력 원본의 작업 사본. 원본 라이브러리 재연동 전에는 삭제하지 말 것 |
| `cache/cv/`, `cache/analysis/` | 재생성 가능한 분석 결과. 재생성 시 API 비용 발생 가능 |
| `glossaries/` | 작품 메모·용어집. 백업 |
| `inbox/image_generation/` | 외부 생성 결과 수신 |
| `exports/` | 내보낸 zip. config.json의 exports_dir로 위치 변경 가능 |
| `samples/` | 테스트 이미지. 코드에 포함하지 않음 |
| `legacy/` | 이전 출력과 검증 산출물. 원본을 삭제하지 않고 이전 |
| `backups/` | 이전 전 APFS 복제 백업. 삭제 결정은 사용자에게 맡김 |

입력 페이지는 cache/input의 파일을 상대 symlink로 참조한다. 따라서 프로젝트 폴더만 복사하면 원본 링크가
깨진다. 맥 미니로 이사할 때는 **데이터 홈 전체(특히 projects + cache/input + glossaries)**를 복사한다.
원본 zip은 외장하드에 그대로 둔다. zip 라이브러리 직접 연결은 후속 과제다.

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
