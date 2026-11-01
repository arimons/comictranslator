# Fonts — 위치, 기본 매핑, 추가 후보

이 폴더는 코드 저장소의 폰트 자산 폴더다. `~/ComicTranslator`는 프로젝트·캐시·결과물 데이터 홈이며, 이 폴더와 별개다.

## 현재 기본값 (변경하지 않음)

현재 매핑의 정본은 `pipeline/pipeline_runner.py`, `pipeline/render_text_overlay.py`, `frontend/src/lib.js`다.

- Normal / 기본값: `Freesentation-5Medium.ttf`
- Light: `Freesentation-3Light.ttf`
- Bold: `Freesentation-7Bold.ttf`
- Impact: `Pretendard-Black.otf`
- Serif (`memory_serif`): `SourceHanSerifK-Medium.otf`, 없을 때 RIDIBatang
- Serif Bold: `SourceHanSerifK-Bold.otf`, 없을 때 SemiBold → RIDIBatang
- Handwritten: `NanumGiBbeumBarkEum.ttf`
- EFX (`serif_emphasis`, 호환 `sfx`/`efx`): `a트랙터.ttf`, 없을 때 Pretendard Black
- Etc: 수동 지정 경로. UI의 최초 후보는 `NanumBarunpenB`.
- 자동 분류의 내부 SemiBold: `Freesentation-6SemiBold.ttf` (수동 스타일 버튼 목록과 구분).

## 이번에 추가한 공식 배포본

기존 파일을 덮어쓰거나 기본 매핑을 변경하지 않고 아래 5개를 추가했다.

- `SourceHanSansKR-Regular.otf` — 본고딕 Regular
- `SourceHanSansKR-Bold.otf` — 본고딕 Bold
- `SourceHanSansKR-Heavy.otf` — 본고딕 Heavy
- `NanumSquareRoundL.ttf` — 나눔스퀘어라운드 Light
- `NanumSquareRoundR.ttf` — 나눔스퀘어라운드 Regular

본명조는 기존 `SourceHanSerifK-*.otf`에 SemiBold·Bold·Heavy 등이 이미 있어 KR 서브셋 파일을 중복 추가하지 않았다. 새 파일은 `/api/fonts`의 전체 폰트 후보에 포함되지만, 기존 스타일 버튼에 자동으로 연결되는 것은 아니다.

공식 출처:
- Adobe 본고딕: https://github.com/adobe-fonts/source-han-sans/tree/release/SubsetOTF/KR
- Adobe 본명조: https://github.com/adobe-fonts/source-han-serif/tree/release
- 네이버 공식 웹폰트 목록: https://hangeul.pstatic.net/hangeul_static/css/nanum-square-round.css
- 네이버 공식 라이선스 안내: https://help.naver.com/support/contents/contents.help?serviceNo=1074&categoryNo=3497

라이선스 원문은 `licenses/`에 보관했다. 이번 확인이 기존에 모아둔 모든 폰트의 취득 경로나 사용권까지 검증했다는 의미는 아니다.

## Git / Syncthing 주의

- `.gitignore`가 `Fonts/*.ttf`, `Fonts/*.otf`를 제외한다. 폰트 바이너리는 현재 Git 추적 대상이 아니며, 코드 저장소를 pull하는 것만으로 새 폰트가 다른 기기에 설치되지 않는다.
- 현재 Developer Syncthing 공유에서는 `/NEW_COMIC_TRANSLATOR`가 폴더째 제외되어 있다. 이 문서 작업에서 Git ignore·Syncthing 규칙은 바꾸지 않았다.
- 사용자 요청으로 `sync-conflict` 폰트 사본 32개를 삭제했다. 삭제 직전 대응 원본의 존재와 SHA-256 일치를 다시 확인했으며, 남은 37개 폰트 파일의 내용·수정시각은 보존했다. `/api/fonts` 로컬 테스트에서도 충돌 사본 없이 37개가 표시되는 것을 확인했다.

## 검증 범위

- 새 5개 파일은 공식 배포본과 SHA-256 일치 및 Pillow 실제 family/style 로딩을 확인했다.
- 최초 폰트 추가 때는 기존 64개 파일의 내용과 수정시각을 보존했다. 이후 별도 승인된 충돌 사본 정리에서는 동일한 사본 32개만 삭제했고, 정상 폰트 37개와 기본 매핑은 그대로 유지했다.
- 데이터 홈을 scratch로 격리한 FastAPI TestClient에서 실제 `/api/fonts` 응답 200과 새 5개 노출, 기본값 Freesentation Medium 유지를 확인했다. 실행 중 서비스나 사용자의 프로젝트 데이터는 이 검증에 사용하지 않았다.
