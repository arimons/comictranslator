# 운영 메모

README에서 분리한 개인 운영 환경 기록이다. 맥 미니 상시 서버와 기기 간 동기화 방식을 다룬다.

## 상시 서버

상시 서버는 PM2의 `comictranslator`로 관리하고, Tailscale Serve HTTPS를 통해 접속한다. 설정은 `deploy/pm2/ecosystem.config.cjs`, 절차는 [PM2 운영 가이드](../deploy/pm2/README.md)를 따른다.

## 코드 동기화

맥북의 코드 수정은 Syncthing으로 맥 미니에 동기화된다. Python 변경은 동기화 완료 후 맥 미니에서 `pm2 restart comictranslator`로 반영한다. 실행 중 작업을 보호하기 위해 PM2 watch는 꺼져 있다.

프론트엔드를 수정했다면 먼저 빌드한다.

```sh
npm --prefix frontend ci
npm --prefix frontend run build
# web/ 동기화 완료 후 브라우저 새로고침
```

`.venv`, `node_modules`, `.git`은 기기별로 관리한다. 새 Python 의존성은 대상 맥의 `.venv`에도 설치해야 한다.

## 데이터 홈

데이터 홈 `~/ComicTranslator`는 현재 Syncthing 자동 동기화 대상이 아니며, 실제 운영 데이터는 맥 미니를 기준으로 사용한다.

```text
~/ComicTranslator/
├── projects/    project.json, 분할 입력, 편집 상태, 최종 출력
├── cache/       CV·분석·LaMa 모델 및 기존 입력 호환 사본
├── glossaries/  작품 메모·용어집
├── exports/     내보낸 파일
├── local/hosts/ 호스트별 원본 폴더 연결
└── legacy/      보존한 이전 작업물
```

일반 페이지는 원본을 읽기 전용으로 참조하고, 분할·크롭 결과만 프로젝트가 소유한다. 맥 미니에서 경로가 다르면 **원본 연결**에서 폴더를 지정하고 해시가 일치하는 파일만 재연결한다. CV·분석 캐시와 기존 JSON 호환은 유지하며 `cache/input`을 일괄 삭제하지 않는다. 자세한 내용은 [원본 참조·재연결](INPUT_SOURCES.md)과 [저장 구조와 이전](STORAGE_LAYOUT.md)을 본다.

## 개발 검증

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```
