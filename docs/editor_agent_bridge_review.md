# 편집기 에이전트 연결 검토

2026-10-19 · 코드와 실제 앱 실행을 근거로 작성한 구현 전 검토 기록. 현재 구현 상태는 아래 후속 안내와 정본 문서를 따른다.

**후속 구현:** 같은 날 조회 3개와 제안 저장 1개의 stdio MCP를 구현하고 Codex/Claude의 프로젝트 로컬 설정에 등록했다. 현재 기능·실행법은 [편집기 검토 MCP](editor_mcp.md)가 정본이다. 아래는 구현 전 검토 기록이며, 실제 1단계는 UI·FastAPI를 import하지 않는 로컬 산출물 어댑터로 분리했다. preview·자동 적용은 후속 범위다.

이 앱은 이미 FastAPI로 영역과 편집 기능을 제공하므로 에이전트가 관찰하고 수정 후보를 제안하는 통로를 만들 수 있다. 우선 **원본·분석·마스크·OCR sheet·최종 결과와 JSON을 함께 읽는 도구**부터 연결하는 것이 적합하다. 이미지 클릭을 자동화하는 것보다 영역 ID와 원본 좌표를 전달하는 방식이 안정적이다.

MCP는 이 통로를 도구와 데이터 형식으로 공개하는 규약이다. 판단 로직은 별도로 필요하다. 도구는 이미지와 구조화된 결과를 함께 반환할 수 있으므로, 앱의 이미지를 실제로 보는 관찰과 JSON 검사를 결합할 수 있다. [MCP 공식 Tools 명세](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

## 판단의 기준: 처리 기준과 생성 방법을 분리

- **경계 기반 (`boundary_based`)**. 일반 FloodFill과 Edge barrier 경로 등을 포함한다. 기존 저장 값은 `type: bubble`이다.
- **텍스트 기반 (`text_based`)**. 텍스트 위치·획을 중심으로 범위를 구성한다. 기존 저장 값은 `type: unboxed`이다. “말풍선 밖이 정답”이라는 뜻은 아니다.
- 모든 화면 영역 ID는 **R 번호 + 처리 기준 배지**다. `processing_basis`는 처리 기준, `source_method`는 검출 방법이다. Edge를 세 번째 타입으로 만들지 않는다.
- 기존 JSON·캐시 호환과 변수명 변경은 [영역 처리 기준 변경 기록](REGION_PROCESSING_BASIS_CHANGELOG.md)을 따른다.
- “검출이 완료됨”, “API가 성공함”, “마스크 밖 변경 0px”과 “사람 눈에 적절함”을 구분한다.

근거는 `frontend/src/EditorCanvas.jsx`, `pipeline/dialogue_extractor.py`다. 실제 00008 결과에는 `flood` 및 `manual_edge_barrier_rescue`로 얻은 경계 기반 영역, `bbox_fallback`으로 얻은 텍스트 기반 영역가 함께 있다. 이 기록은 분류 정확도의 정답 라벨이 아니다.

## 현재 API를 어디까지 쓸 수 있나

| 목적 | 기존 경로 | 연결 시 주의점 |
|---|---|---|
| 프로젝트 목록 | `GET /api/sessions/recent` | 이미지 hash의 포함 관계로 프로젝트가 합쳐져 숨겨질 수 있다. 전체 목록 API로 간주하지 않는다. |
| 프로젝트와 페이지 목록 | `GET /api/session/{session_id}` | 내부에서 `ensure_session()`을 호출한다. 존재하지 않는 ID의 조회는 폴더를 만들 수 있으므로 어댑터가 기존 ID인지 먼저 검사한다. |
| 선택 페이지 상세 | `GET /api/session/{session_id}/page/{image_id}` | TG, regions, guidance, integrated, overlay layout, 이미지 URL 등을 얻을 수 있다. |
| 이미지 | `GET /api/file?path=…` | 반환된 파일 URL을 이용한다. 현재 코드는 세션·입력 캐시·분석 캐시 아래 이미지 확장자만 허용한다. 새 도구에는 임의 파일 경로보다 artifact 종류를 받는 편이 적합하다. |
| Guidance 적용 | `POST /api/guidance` | Step 3를 실행하고 guidance·산출물 캐시까지 저장한다. 미리보기 전용 API가 아니다. |
| Overlay 적용 | `POST /api/overlay/apply` | guidance를 저장하고 렌더한다. 병합과 `replace`의 의미를 구분해야 한다. |
| OCR·번역·렌더 | `POST /api/run-final`, `POST /api/run-final-stream` | 외부 API 비용이 생길 수 있다. 관찰 중 자동으로 호출할 도구로 두지 않는다. |
| 복원 정보·자동 마스크 | `GET /api/restore/info`, `POST /api/restore/auto-mask` | 자동 마스크는 기본 저장 동작이다. 초안용이면 `save:false`와 반환 PNG를 이용한다. |
| 복원 실행·진행 | `POST /api/restore/run`, `GET /api/restore/job/{job_id}` | 완료 후 결과를 받아볼 수 있다. 실행도 마스크와 문맥 상태를 저장하므로 완전한 무변경 preview는 아니다. |
| 복원 확정 | `POST /api/restore/accept` | 실행과 확정이 분리돼 있다. 기존 확정 패치 목록에 추가한다. |

서버 구현은 `main_v2.py`, Guidance 데이터 처리는 `pipeline/manual_guidance.py`, 복원은 `pipeline/background_restore.py`를 기준으로 확인했다. 현재 HTTP API는 MCP 서버 자체가 아니다.

## 권장 연결 구조

```text
에이전트
  ↓ 로컬 MCP stdio
Comic Translator 어댑터
  ├─ 기존 페이지·artifact 조회
  ├─ 원본 좌표 기준 crop + 영역 JSON 반환
  ├─ 관찰·수정 제안 초안 저장 (추가 구현)
  └─ 검토용 복사본 실행 / 승인된 초안 적용 (추가 구현)
       ↓
     기존 FastAPI와 파이프라인
```

처음에는 로컬 어댑터를 별도 프로세스로 두어 기존 서버의 API를 호출할 수 있다. 앱 안에 별도 모델 호출 코드를 넣지 않아도, 연결된 에이전트가 도구로 이미지를 받아 관찰할 수 있다. 추후 무인 배치 검토가 필요하면 같은 관찰·제안 형식을 별도 실행기에서 재사용한다.

공용 인터넷에 로컬 API를 공개할 필요는 없다. 원격 에이전트에서 사용할 때는 로컬 연결 실행기가 필요하며, 이미지에 적힌 대사는 데이터로 취급한다. 프로젝트 파일·이미지 속 문구를 실행 지시로 받아들이지 않는다.

### 1단계: 관찰과 제안

아래는 **초기에 제안한 MCP 도구 계약**이다. 현재는 같은 이름의 1단계 도구를 구현했으며 실제 입력·반환 형식은 [편집기 검토 MCP](editor_mcp.md)를 따른다.

| 제안 도구 | 입력 | 반환 |
|---|---|---|
| `list_projects` | 없음 또는 페이지 커서 | 실제 존재하는 프로젝트 ID와 요약 |
| `inspect_page` | session_id, image_id | 원본 크기·hash·revision, TG·영역·번역·layout와 artifact 목록 |
| `view_region` | session_id, image_id, region_id, artifact 종류, 여백 | 원본/마스크/최종 crop 이미지와 원본 좌표·type·생성 방법 |
| `propose_review` | 페이지 revision, 관찰과 후보 목록 | 적용하지 않은 제안 ID, 근거 crop, 확인할 질문 |

이미지는 축소본 한 장으로 끝내지 않고 전체 페이지와 필요한 부분의 원본 해상도 crop을 함께 제공한다. 화면 좌표 대신 원본 이미지의 `[x,y,w,h]`를 사용한다. line과 seed는 각각 원본의 두 점과 한 점으로 정의한다. region ID는 분석을 다시 하면 바뀔 수 있으므로 페이지 revision과 함께 저장한다.

규칙으로 바로 관찰할 수 있는 예는 빈 번역문, 이미지 밖 bbox, 누락 artifact, job 실패, 마스크 밖 변경 픽셀 수다. 반면 “여기가 실제 말풍선 경계다”, “두 대사로 나눠야 한다”, “경계 기반보다 텍스트 기반이 적합하다”, “복원한 망점이 자연스럽다”는 이미지 해석이다. 이 경우 관찰과 가능한 해석, 필요한 사용자 확인을 반환한다. 현재 AGENTS.md의 더블체크 규칙도 그대로 적용한다.

제안 데이터는 다음 정도의 구조로 시작할 수 있다. 실제 검출 결과가 아닌 형식 예시다.

```json
{
  "session_id": "existing-session",
  "image_id": "existing-page",
  "base_revision": "snapshot-revision",
  "coordinate_space": "original_pixels",
  "observations": [
    {
      "region_id": 4,
      "stored_type": "unboxed",
      "source_method": "bbox_fallback",
      "evidence_artifact": "original_crop",
      "interpretation": "경계를 더 확인해야 함"
    }
  ],
  "candidate_actions": [],
  "needs_human_check": true,
  "quality_verdict": "unreviewed"
}
```

`interpretation`에는 예를 들어 “경계를 더 확인해야 함”처럼 관찰과 연결된 해석을 적는다. 모델이 점수 하나를 출력했다고 자동 승인하지 않는다. 초기에는 추정 확률을 품질 점수로 쓰지 않고, 사용자가 채택·수정·기각한 기록으로 판단을 검증한다.

### 2단계: 복사본 미리보기와 확정

기존 API를 그대로 전부 공개하면 “제안”과 “실제 파일·공용 캐시 변경”이 섞인다. 다음 부분을 추가한 뒤 쓰기 도구를 연결하는 것이 적합하다.

1. 제안을 별도 파일에 저장하고 원본 guidance는 유지한다.
2. Step 3·Overlay 검토는 공용 캐시를 갱신하지 않는 독립 work_dir에서 실행한다. 세션 복사만으로 충분한지 확인하고 캐시 저장·backfill도 차단한다.
3. artifact hash 또는 내용 기반 revision을 받아 적용 직전에 잠금 안에서 다시 검사한다. 현재 `detail_rev`는 UI 갱신 정보이지 쓰기 충돌을 거부하는 계약이 아니다.
4. 적용 시 before/after와 변경 내역을 보관한다. 같은 요청을 다시 보내도 중복 실행하지 않도록 proposal ID를 사용한다.
5. `inspect` / `propose` / `preview` / `apply` 권한을 나눈다. 처음에는 apply를 열지 않는다. 유료 OCR·이미지 생성은 별도 실행 의사와 예산이 있을 때만 연결한다.
6. 긴 작업은 job ID로 시작하고 완료까지 조회한다. 복원 job은 현재 메모리에서 관리되므로 서버 재시작 후 이어서 볼 수 있는 기록도 필요하다.

## 매뉴얼을 판단 지식으로 재사용하는 방법

매뉴얼 HTML만 긴 프롬프트로 넣기보다, 동일한 내용을 다음 형식의 사례 묶음으로 관리하는 편이 검증하기 쉽다.

- 문제 후보와 관련 도구: TG 묶음 / R 경계 / U 글자 마스크 / 번역문 / 배치 / 배경 복원.
- 사례마다 원본 hash, 분석 revision, 영역 ID, 원본 좌표, 적용 전후 artifact를 보관한다.
- `조작 예시`, `실행 관찰`, `사용자 검증 완료 사례`를 구분한다.
- 사용자 검증이 끝난 사례만 판단 기준의 예시로 쓴다. TG를 임의로 가른 조작 스크린샷을 “분리해야 하는 정답”으로 학습시키지 않는다.
- 아직 검증하지 않은 사례는 기준 확정에 쓰지 않고 사용자가 확인할 후보로 반환한다.

처음부터 자동 보정을 평가하기보다, 사용자가 고른 수정 사례에서 “문제 후보를 찾았는가”, “관련 도구를 적절히 제안했는가”, “필요 없는 수정을 얼마나 제안했는가”를 확인한다. 처리 기준 분류의 시각적 정답과 복원 품질은 사용자가 확인한 라벨을 기준으로 평가한다.

## 이번 실제 실행에서 확보한 근거와 한계

- `docs/editor_manual.html`에 실제 앱 화면과 사용 기준을 추가했다. 00006의 TG Off·Split·경계 기반 seed·텍스트 기반 영역·Region Off 화면은 **적용 전 조작 예시**다.
- 00008 저장 JSON에서 경계 기반 영역의 일반 FloodFill / Edge barrier 경로와 텍스트 기반 영역의 bbox fallback을 구분했다. 이 분류가 시각적으로 옳다는 판정은 하지 않았다.
- 복사 세션 `20261019_222000_manual`에서 당시 U4(현재 R4·텍스트 기반) 기반 자동 마스크와 수동 문맥으로 LaMa를 실행했다. 마스크 4,959px, 문맥 밖 제외 0px, 마스크 밖 변경 0px, 추론 약 4.5초였다. 속도는 해당 로컬 실행 기록이며 보장값이 아니다.
- 결과 패치는 확정하지 않았다. OCR/번역 및 이미지 생성 API는 새로 호출하지 않았다.
- 현재 복원 UI는 job이 done이어도 비교부에 “결과 없음”이 보이고 결과 crop 대신 원본·마스크만 보였다. 코드상 polling 결과로 job을 교체하며 클라이언트의 `batch`가 사라질 가능성이 있다. 이번 범위에서는 수정하지 않았으며, 매뉴얼에는 저장된 before/after 산출물 자체를 넣었다.
- 실제 캡처와 생성 스크립트는 `proof/misc/editor_manual/`, `proof/src/update_editor_manual_examples.py`에 있다. proof 생성물은 정리될 수 있으므로 최종 매뉴얼에는 이미지를 내장했다.

다음 구현 범위로는 **읽기 도구 3개와 제안 저장 도구 1개**가 적합하다. 이것으로 에이전트의 1차 관찰과 사용자 확인 흐름을 검증한 뒤, 복사본 preview와 적용 기능을 확장할 수 있다.
