# 영역 참조(region_ref) 설계안과 리뷰 기록

2026-10-29〜30 · 설계(Opus) → 구현(Sonnet) → 적대적 리뷰 4회의 기록. 커밋 범위 `273f1cb..009dbbc`, main과 맥미니 운영 서버에 반영됨. 사용자가 실데이터 세 페이지(00011·00005·00004)로 동작을 확인했다.

현재 동작의 정본은 `docs/CODEBASE_FILE_MAP.md`(영역 참조 항목)와 `pipeline/region_refs.py`다. 이 문서는 **왜 이렇게 만들었는지**와 **리뷰에서 무엇이 나왔는지**를 남기는 시점 기록이다. 설계 당시 문장 중 구현으로 바뀐 것은 "구현 중 바뀐 결정"에 모았다.

## 1. 문제와 결론

영역 R 번호는 재분석마다 읽기 순서로 다시 매겨지는데, 번역(`source_region_id`)·Overlay 수동값·화자·메모가 이 번호를 키로 쓰고 있어 다른 영역에 붙는 사고가 났다. 실데이터(164페이지, 영역 936개)에서 번역 번호와 현재 번호가 어긋난 페이지가 00005·00009·00011 세 곳이었다.

00011 사고 재구성: 번역 당시 R1=TG1, R2=TG2, R3=TG3. 이후 TG2를 끄고 R2(TG2)를 제외해 현재 R1=TG1, R2=TG3. Overlay 키 `"2"`(빈 text_override로 숨김)와 `"3"`(폰트)이 번호로만 붙어 TG3의 번역이 엉뚱하게 처리됐다. 오래된 번역 경고도 `regions_signature` 도입 전이라 뜨지 않았다.

설계 결론은 세 가지다.

1. **키를 둘로 나눈다.** 번역·Overlay 수동값·화자·메모는 "무슨 대사인가"를 가리키는 **내용 참조 `region_ref`**(TG 집합)로, 영역 제외·LaMa 고정은 "어떤 처리 인스턴스인가"를 가리키는 **인스턴스 참조 `region_instance_ref`**(출처 + region_ref)로 묶는다. 00021·00022·00015는 자동 영역을 제외한 뒤 같은 TG로 수동 Edge/ubox 영역을 다시 만들었다. 제외를 TG 집합만으로 묶으면 새로 만든 영역까지 제외되고, 반대로 번역·수동값은 자동→Edge 대체 뒤에도 따라가야 한다.
2. **TG 없는 영역은 사각형 중심 대신 가이드 ID를 쓴다.** 수동 가이드는 이미 안정 ID(`manual_seed_<ts>`, `manual_ubox_<ts>`, `edge_box_<ts>`, `split_<ts>`)가 있다. TG 없는 영역 18개는 seed 10, 수동 ubox 1, 자동 분할 자식 7이었다.
3. **합침·분할은 키 동일성만으로 풀리지 않는다.** 해석기(resolver) 하나를 Python에 두고 모든 소비처가 그 결과만 쓴다. 짝을 못 찾으면 **다른 번호로 fallback하지 않고** 보관함(`unmatched_regions`)으로 보낸다.

## 2. 설계 당시 현황 조사 (맥미니 164페이지 프로젝트, 읽기 전용)

- 한 페이지 안에서 두 영역이 같은 TG를 공유한 경우 0건. TG 여러 개 영역 46개. `tg_splits` 사용 0건.
- 화자 `by_region`은 이미 `region_binding_key`(처리 기준·검출 방법·사각형) 키였다. 사각형이 바뀌면 끊어지고 `by_tg`로 떨어진다.
- 제외 기록 `target_key`도 사각형 키다. 00021은 제외한 Edge 사각형과 현재 Edge가 달라 제외가 더는 적용되지 않는 상태였다(의도인지 미확인).
- 수동 가이드 ID는 regions.json까지 전달되지 않았다. 수동 영역은 `region_id` 10000+i, Edge 11000+i, seed 12000+i라는 목록 순서 기반 임시 번호만 `source_order`로 남았다.

번호를 키로 쓰던 지점(참조 지점 전수 목록의 요약):

| 영역 | 번호 생성/사용처 |
|---|---|
| Step 3 | `dialogue_extractor.py`가 최종 번호 `region_id = i + 1`(읽기 순서)을 만든다. 제외·LaMa 고정의 `target_key`는 사각형 키이고, 오래된 항목은 번호 비교로 내려간다 |
| Step 4 | `gemini_ocr_translate_v5.py`가 `sentence_id = region_id*100 + seg`, `source_region_id`를 저장 (시트 라벨 번호, 같은 실행 안에서만 유효) |
| 렌더러 | `render_text_overlay.py`의 override 조회·`region_id_for_cluster`·synthetic cluster·`restore_untranslated_regions`가 번역 번호와 현재 번호를 한 dict에서 섞었다 (**번호 공간 혼용**, 사고의 직접 원인) |
| 화자·메모 | `work_glossary.py`는 binding key → `by_tg` |
| 앱 | `app.py`의 overlay stamp/remap, `/api/speakers`, `/api/region-context` |
| 프런트 | `lib.js`의 `overlayControlItems`·`regionCluster`·`overlayItemKey`, `OverlayCanvas.jsx`, `EditorCanvas.jsx`, `App.jsx`의 선택·화자·Overlay 핸들러 |
| MCP | `editor_review_bridge.py` 요청 `region_id` (`base_revision` 가드가 있어 같은 시점 번호는 안전) |

## 3. ID 정의

기존 API 필드 `region_key`(= binding key)와 `target_key`가 있으므로 새 개념은 `region_ref`, `region_instance_ref`로 부르고 기존 이름의 의미는 바꾸지 않는다.

### 3.1 `region_ref` (내용 참조)

우선순위대로 하나를 고른다.

1. `tg:<정렬된 TG id를 쉼표로>` — `anchor_ids`가 있으면. 예 `tg:3`, `tg:10,13`. 수동 가이드로 만든 영역도 TG가 있으면 이 형식이다.
2. `tg:<부모 TG 집합>/s<split_order>` — TG 없는 분할 자식. 부모 TG 집합은 같은 `split_parent_rect`를 가진 형제들의 `anchor_ids` 합집합이다(구파일에서도 파생). 새 Step 3는 `split_parent_anchor_ids`를 직접 기록한다.
3. `guide:<가이드 id>`(+ 분할 자식이면 `/s<split_order>`) — TG 없는 수동 영역(seed·수동 ubox·수동 bubble·Edge box).
4. `geo:<b|t>:<cx>,<cy>` — 위 셋이 모두 불가할 때만. 처리 기준 1글자 + 사각형 중심 정수. 동일성 비교가 아니라 허용오차 매칭(A6)으로만 해석하며 `region_ref_weak: true`를 표시한다.

세그먼트 수동값 키는 `<region_ref>#<source_segment_index>`(예 `tg:10,13#2`)다. 구키 정규식 `^\d+([:\-]\d+)?$`과 겹치지 않아 한 dict에서 구키와 신키를 구분할 수 있다.

### 3.2 `region_instance_ref` (인스턴스 참조)

`<origin>|<region_ref>`. origin은 `auto` 또는 가이드 id(`edge_box_…`, `manual_ubox_…`, `manual_seed_…`, 수동 bubble id)다. 자동 영역은 검출 방법(flood/bbox_fallback)과 처리 기준을 넣지 않는다. 벽을 추가해 자동 검출 방법이 바뀌어도 같은 TG의 자동 영역은 같은 인스턴스로 본다.

### 3.3 페이지 내 충돌

같은 `region_ref`가 두 영역에 생기면(실데이터 0건, 코드상 불가능 보장은 못 함) 자동 영역이 기본 키를 갖고 수동 영역은 `@<가이드 id>`를 붙인다(`tg:6@edge_box_…`). 그래도 겹치면 읽기 순서로 `@n`을 붙이고 `region_ref_collision: true`와 로그를 남긴다. 충돌 키는 약한 참조로 취급한다.

### 3.4 Step 3·4 출력에 덧붙인 필드 (알고리즘 변경 없음)

- `regions.json`: `region_ref`, `region_instance_ref`, `region_origin`, `split_parent_anchor_ids`(분할 자식), `region_ref_weak`/`region_ref_collision`(해당 시). 가이드 id를 영역까지 전달한다(수동 bubble item `id`, 수동 ubox, Edge `box_id`, seed `id`, `split` guide `id`).
- `integrated.json`: cluster마다 `source_region_ref`, 최상위 `region_ref_scheme`·`missing_region_refs`.
- `region_id`, `source_order`, `type`, `source_region_id`, `sentence_id`, 좌표 키는 그대로 둔다.

### 3.5 구 파일에서 파생 (파일은 다시 쓰지 않음)

`with_processing_basis`처럼 메모리 projection으로 붙인다. `tg:`는 `anchor_ids`로 바로 얻는다. origin은 다음처럼 guidance에서 가이드 id를 찾는다: `manual_ubox_guide`는 `manual_regions[].unboxed_rect`와 일치(IoU 0.9 이상), `manual_bubble_seed_rescue`는 seed point가 영역 안(여럿이면 가장 가까운 것), `manual_edge_barrier_rescue`는 `box_id`별 벽 합 사각형이 영역 중심을 포함. 그 외는 `auto`. 파생에 실패한 TG 없는 영역은 `geo:`.

## 4. 해석기 규칙

`pipeline/region_refs.py`(순수 함수, I/O 없음). 해석 결과 `kind`는 `exact`, `shrink`, `merge`, `split`, `geo`, `unmatched` 여섯 가지다.

- 해석 순서: 정확히 일치 → TG 관계(shrink/merge/split) → geo(허용오차) → TG digest가 바뀐 경우에만 IoU 0.6 이상 → unmatched.
- **번호 fallback 금지.** `source_region_id`나 `"N"`은 같은 integrated 파일 안에서 묶음을 찾는 데만 쓴다.
- snapshot은 참조를 저장할 때 같이 남기는 `{region_ref, tg_ids, rect, region_id, tg_digest}`다. overlay는 기존 `_anchor`를 이 형태로 확장했다.
- integrated 구파일의 ref 파생: 같은 `source_region_id`를 가진 cluster들의 `matched_cluster` 합집합을 번역 당시 TG 집합으로 보고 `tg:…`로 만든다. 합집합이 비면 `source_region_rect`와 현재 영역을 IoU 0.6 이상으로 단일 매칭하고, 실패하면 `geo:`.
- 프런트는 해석기를 다시 구현하지 않는다. 서버가 page payload에 결과(`overlay_guidance_v1`, `resolved_region_ref` 등)를 붙인다.

## 5. 정책 A1-A14

사용자 확인 없이 기본값으로 정했고 정책 상수 한곳(`pipeline/region_refs.py`의 `POLICY`)에 모았다. 해석기 결과 `kind`와 소비처(`overlay`/`speaker`/`context`)의 조합 표 하나이므로, 뒤집을 때는 표만 바꾼다(A9).

| 번호 | 내용 | 근거 |
|---|---|---|
| A1 | **분할**: 화자·번역 메모는 자식 모두에 복사(`copy_all`). Overlay 수동값은 값 전체를 보관함으로 보내고 보고(`stash`) | 화자·메모 오류는 번역 프롬프트에만 영향을 주고 화면 검사 패널에서 바로 고칠 수 있다. Overlay 값은 틀리면 최종 이미지에 조용히 박히고, 위치·크기·text_override가 영역 모양에 묶여 있어 일부만 복사하면 값이 쪼개진다 |
| A2 | **합침**: 옛 참조가 하나만 들어오면 그대로 이동(`move_if_sole_source`). 둘 이상이면 화자는 모두 같을 때만 유지하고 다르면 비워 `by_tg`·기본값으로 내려가게 둔 뒤 보고, 메모는 같으면 유지하고 다르면 전부 보관, Overlay 값은 전부 보관(`merge_conflict`) | 서로 다른 지정을 자동으로 고르면 "조용히 다른 값이 붙는" 지금 사고와 같은 종류가 된다 |
| A3 | **축소**(옛 TG 집합의 일부만 남음): 남은 영역이 하나면 모든 참조를 옮기고 `_migrated: shrink` 표시 | 00011처럼 TG 하나를 끈 경우가 가장 흔하고 남은 대사는 같은 말풍선이다 |
| A4 | **제외·LaMa 고정은 재조정하지 않는다.** 인스턴스가 사라지면 기록은 맞지 않을 뿐 다른 영역에 옮겨 붙이지 않는다. 00021처럼 이미 안 맞는 기록도 그대로 둔다 | 제외는 "이 처리 인스턴스를 끈다"는 뜻이다. 옮기면 사용자가 새로 그린 영역이 사라진다 |
| A5 | 자동 영역의 origin은 `auto` 하나 | 벽을 추가해 검출 방법이 바뀌어도 기존 제외가 풀리면 안 된다. 기존 "R 제외 시 그 TG의 fallback도 막는다" 정책과 맞는다 |
| A6 | `geo:` 허용오차: 중심 거리가 짧은 변의 25% 이하, IoU 0.5 이상. 후보가 둘 이상이면 매칭하지 않는다. TG 전면 변경 시 IoU fallback 기준은 기존 `MIN_OVERLAP` 0.6 | 기존 overlay_anchor 기준에 맞춤. 실데이터 사례가 거의 없어 조정 근거 없음 |
| A7 | TG 번호 전면 변경 감지: TG id·사각형 목록 해시를 snapshot에 `tg_digest`로 남기고 다를 때만 IoU fallback을 연다 (구현에서 기준 파일이 바뀜, 6절 참고) | 평소 IoU 재매칭은 다른 말풍선을 집을 위험이 있어 꼭 필요할 때만 쓴다 |
| A8 | 이중 기록(신형 `regions_by_ref` + 구형 `regions`)은 단계 9까지 유지, 종료 시점은 사용자 결정 | 롤백 가능성을 잃는 비용이 크고 이중 기록 비용은 작다 |
| A9 | A1-A7의 동작을 `POLICY` 표 한곳에 모은다 | 뒤집을 때 표만 바꾸도록 |
| A10 | `regions_signature`(오래된 번역 경고)는 사각형 기반 그대로 | ref 기반으로 바꾸면 기존 페이지의 경고 상태가 일제히 바뀐다. 이 작업과 별개 |
| A11 | 번역 프롬프트·시트 라벨·`sentence_id`는 번호 그대로 | 같은 실행 안에서만 쓰이고 OCR 응답 매칭과 시트 호환 계약이 번호에 의존한다 |
| A12 | MCP는 `region_id`를 유지하고 `region_ref`를 선택 인자로만 추가 | `base_revision` 가드가 이미 있어 번호가 바뀌면 제안이 거부된다 |
| A13 | 회귀 fixture는 합성 JSON. 실제 만화 번역문을 저장소에 넣지 않는다. 실데이터 확인은 맥미니 프로젝트를 scratch에 복사해서만 | |
| A14 | 분할 자식에 세그먼트 없는 번역이 붙는 자리: TG가 가장 많이 겹치는 자식 하나, 동률이면 읽기 순서가 앞인 자식. 나머지는 번역 없음으로 두고 `restore_untranslated_regions`가 원본을 복원 | 같은 문장을 두 자식에 겹쳐 그리면 화면이 깨진다. 원본 복원이 빈칸보다 안전하다 |

### 반례 검증표 (설계 §3.6)

| 상황 | 결과 |
|---|---|
| 재번호만 | ref 불변, 그대로 붙음 |
| 자동 → Edge/수동 ubox 대체(같은 TG) | `region_ref` 같고 `instance_ref` 다름. 번역·수동값·화자 유지, 자동 영역 제외는 새 영역에 적용 안 됨 |
| 합침 `{10}`,`{13}` → `{10,13}` | 각 옛 ref가 새 영역의 부분집합. A2 적용. 번역 조각은 TG로 붙고 기존 `ocr_stale` 경고가 뜸 |
| 분할 `{10,13}` → `{10}`,`{13}` | 번역은 세그먼트 `matched_cluster`(TG 단위)로 각 자식에 붙음. 세그먼트 없으면 A14. 나머지는 A1 |
| 부분 겹침 `{2,3}` → `{3}` | 이동 + `_migrated: shrink` (A3) |
| TG 완전 소멸 | 보관 (00011의 `"2"`) |
| TG 없는 seed 영역 | `guide:manual_seed_…`. seed 삭제 시 보관, 이동(같은 id)이면 유지 |
| TG 없는 자동 분할 자식 | `tg:<부모>/s<n>`, 부모 TG와 분할 순서가 같으면 유지 |
| 재번역 | Step 4가 `source_region_ref` 기록. 세그먼트 수가 줄어 `#k`가 사라지면 그 세그먼트 값만 보관 |
| CV/Step 2 재실행으로 TG 번호 전면 변경 | 모든 `tg:`가 무효. digest가 바뀐 경우에만 수동값 `_anchor.rect`로 IoU 재매칭 |
| `tg_splits`로 TG 분할 | 새 TG id는 max+1부터 분할 목록 순서. 앞 분할을 지우면 뒤 TG id가 바뀐다(잔여 위험, 실사용 0건) |

## 6. 구현 중 바뀐 결정

구현 세션이 설계와 다르게 정한 것과 그 판정이다. 코드(`pipeline/region_refs.py`)와 대조했다.

- **(a) TG가 사라진 묵은 번역은 렌더하지 않는다.** TG 비활성화·영역 제외로 현재 어느 영역에도 없는 번역 조각은 그리지 않는다(`RENDER_VANISHED_GROUP_CLUSTERS`). 00011에서 사용자가 빈 text_override로 숨기던 것을 자동화한 동작이며, 제외한 영역의 번역도 안 그리는 것이 제외의 뜻과 맞는다. 리뷰 판정: 맞음.
- **(b) 묶음 없는 번호 키는 `_anchor`가 안 맞으면 보관.** 다른 영역에 붙지 않는 점은 맞지만 정당한 직접 입력까지 보관하는 문제가 있어(리뷰 지적 6) 이후 보강했다: overlay가 `regions.json`보다 나중에 저장됐고 그 R이 번역이 없을 때만 현재 R 번호로 해석한다.
- **(c) 번역 없는 페이지의 구형 키는 번호 그대로 저장.** 효과가 없었고(읽기·렌더에서 보관됨), (b) 보강으로 대체됐다.
- **(d) 번역 조각을 자기 TG로 먼저 해석.** 맞음. 분할 때 세그먼트가 자식에 붙는다.
- **(e) 약한 참조는 구형 키로 판단.** 맞음. `method:` origin도 약한 참조에 포함해야 했다(지적 1).
- **(f) 폐기.** 처음에는 가이드 id를 모르는 구 파일 수동 영역의 origin을 `method:<방법명>`으로 두는 결정이었다. 구 파일 수동 영역을 제외·LaMa 고정해도 첫 재분석에서 무시되는 오류로 판명(지적 1)되어, 설계 §3.5대로 **guidance(`manual_regions`·`manual_seed_points`·`virtual_walls`)에서 가이드 id를 파생**한다. 파생에 실패한 경우에만 `method:<방법>`을 쓰며, 이는 약한 참조라 제외·고정 판정이 구형 `target_key`/번호 규칙으로 내려간다. 렌더러도 `regions.json` 옆 `*_manual_guidance.json`으로 같은 파생을 한다(N1).
- **TG digest 기준(A7 변경).** 설계는 `text_groups_effective.json` 기준이었으나, TG 하나를 끄거나 분할하기만 해도 digest가 바뀌어 페이지 전체 수동값이 위치 매칭으로만 해석되고 줄어든 영역 값이 보관되는 문제(재검토 v3 S)가 있었다. 지금은 **원본 `*_text_groups.json`(CV/Step 2 산출) 기준 `raw:` 접두사 digest**다. TG 끄기·분할은 effective만 바꾸므로 digest가 같아 shrink/merge/split이 그대로 동작하고, 원본 TG가 새로 매겨져 digest가 달라졌을 때만 위치(IoU 0.6)로만 찾으며 못 찾으면 보관한다. `raw:`가 없는 예전 digest는 비교하지 않고, 그 때문에 `tg_digest_changed`로 보관된 값은 축소·합침으로도 되돌아온다. 앱(`_current_tg_digest`)과 렌더러(`_load_raw_text_groups`)가 같은 digest를 계산한다.
- **구 번들 호환.** 새 번들은 저장 요청에 `key_scheme: "region_ref_v1"`을 달아 보내고, 이 표시가 있으면 빈 `regions`로 replace해도 모두 지운다. 표시 없는 요청은 구 번들로 보고 숫자 키를 변환해 받으며, 구형으로 표현 못 하는 참조 전용 값은 지우지 않는다. 번역 없는 영역의 직접 입력은 구형에서 현재 R 번호 키를 쓰되 번역 번호 키와 겹치면 구형에는 쓰지 않는다.

## 7. 구현 단계 (커밋)

| 단계 | 내용 | 커밋 |
|---|---|---|
| 1 | 회귀 fixture와 특성 테스트(00011 구조를 합성 JSON으로) | `cd0dfd0` |
| 2 | `region_refs.py` 순수 모듈 + 단위 테스트 | `b83f7fd` |
| 3 | 읽기 projection (`project_page`, 파일 쓰기 없음) | `1ad88e4` |
| 4 | Step 3·4 출력 필드 덧붙이기 | `5671910` |
| 5 | 렌더러 전환 (번호 fallback 삭제, 번호 공간 혼용 4곳 해소) | `6e5a3ae` |
| 6 | Overlay 저장 전환 + 프런트, 서버와 번들 같은 커밋 | `b4042f7` |
| 7 | 화자·메모 전환 | `ace0dac` |
| 8 | 제외·LaMa 고정 전환, 선택 유지, MCP `region_ref` | `43e936d` |
| 9 | 정리(IoU remap 축소, 이중 기록 종료 결정) | 미진행 |

리뷰 수정: `8bd17f0`(지적 1), `2eab6ed`(지적 2·3·4·6·7), `71f03bd`(지적 5·8), `2626b1f`(N1·N2), `23cef09`(메모 보관·TG digest 앱 연결), `009dbbc`(TG digest를 원본 기준으로).

## 8. 적대적 리뷰 요약

각 회차 모두 합성 fixture 재현과 일부 실데이터(복사본, 읽기 전용) projection으로 확인했다.

### v1 (273f1cb..43e936d)

| # | 문제 | 처리 |
|---|---|---|
| 1 | 구 regions.json 페이지에서 수동 영역을 제외·LaMa 고정하면 첫 재분석에서 무시됨(`method:` origin이 강한 참조로 취급되어 Step 3의 `guide_id` 붙은 참조와 불일치) | `8bd17f0` — guidance에서 가이드 id 파생, `method:`는 약한 참조 |
| 2 | 구 번들 `replace=true` 저장이 번역 없는 영역의 직접 입력을 지움(번역 번호·현재 R 번호가 구형 키에서 겹침) | `2eab6ed` |
| 3 | 롤백 감지(`legacy_digest` 불일치) 시 `regions_by_ref`에만 있던 값이 버려짐 | `2eab6ed` — 구형으로 표현 못 하는 값은 보관 |
| 4 | 재분석 일시 상태로 보관된 Overlay 값이 돌아오지 않음(화자·메모는 돌아오는데 비대칭) | `2eab6ed` — 원래 참조와 문자열이 같고 자리가 비면 `restored` |
| 5 | 합침 때 서로 다른 번역 메모가 보관 없이 비워짐(A2와 다름) | `71f03bd`(reconcile 보관), `23cef09`(저장 API가 보관분 유지) |
| 6 | 구 코드의 번역 없는 영역 직접 입력(`_anchor` 없음)이 보관함으로 빠짐 | `2eab6ed` — 위 (b) 보강 |
| 7 | 재번역으로 세그먼트가 줄어도 `#k` 값이 보관 안 되고 남음(T8 미구현) | `2eab6ed` — `segment_missing` 보관, 다시 생기면 복귀 |
| 8 | MCP `inspect_page`의 integrated에 영역 참조가 안 붙음 | `71f03bd` |
| 9 | 테스트 공백(구 projection→Step 3 경로, T8, T10, T11 등) | 지적 3·4·6·7 테스트 추가. 일부는 v2에서 부분 미충족으로 남음 |

실데이터 확인: 00011 사고는 `"2"`→보관, `"3"`→`tg:3`(현재 R2)로 재현되지 않았다. 00005 R5(수동 ubox, 번역 없음)는 구 코드가 번역 번호 5를 오인해 빈칸으로 두던 것이 새 동작(원본 복원)으로 바뀐다.

### v2 (43e936d..71f03bd)

지적 1·2·3·6·7·8은 해결, 4·5·9는 부분 해결로 판정. 새로 나온 문제:

| # | 문제 | 처리 |
|---|---|---|
| N1 | 구 파일의 TG 없는 수동 영역 참조가 앱(`guide:`)과 렌더러(`geo:`)에서 달라 직접 입력이 렌더되지 않음 | `2626b1f` — 렌더러도 `*_manual_guidance.json`으로 가이드 id 파생 |
| N2 | 새 번들이 마지막 남은 값을 지워도 구형으로 표현 못 하는 값이 안 지워짐(빈 `regions`를 구 번들로 판정) | `2626b1f` — 새 번들 요청에 `key_scheme` 표시 |
| R1 | TG 전면 재부여 뒤 보관 값이 다른 말풍선으로 돌아옴(앱 경로가 `current_tg_digest`를 안 넘김) | `23cef09`로 앱 경로에 연결, `009dbbc`로 기준 정리 |
| R2 | 충돌 접미사(`@n`) 참조가 보관됐다가 다른 영역이 같은 접미사를 받으면 그쪽으로 복귀 | 미처리(낮음) |
| R3 | 재분석 전에 열린 구 번들 탭이 옛 R 번호로 직접 입력을 보내면 다른 미번역 영역에 붙을 수 있음 | 미처리(낮음) |

### v3 (2626b1f, 23cef09)

N1·N2·지적 5 해결, R1은 부분 해결에 새 문제 2건.

| # | 문제 | 처리 |
|---|---|---|
| S | TG 하나만 꺼도(effective 기준 digest가 변함) 페이지 전체 수동값이 IoU로만 해석되고 줄어든 영역 값이 보관됨 | `009dbbc` — 원본 TG 기준 `raw:` digest |
| B | 렌더러가 digest를 안 넘겨, 앱이 보관한 값을 같은 참조 문자열의 다른 말풍선에 다시 붙임 | `009dbbc` — 렌더러도 원본 TG로 같은 digest |

### v4 (009dbbc)

S·B 해결. 회귀(지적 1·2) 재확인. 맥미니 서버 재시작 가능으로 판정했다.

- 재시작 후 권장: 일괄 "처음부터 다시 분석"(force)을 먼저 돌리지 않고 일반 재분석이나 적용으로 digest를 찍는다. 재시작 뒤 브라우저 탭을 새로고침해 새 번들을 받는다.
- 첫 확인 페이지: 00011 p02(사고 페이지), 00005 p01(수동 ubox·LaMa 고정, R5 원본 복원), 00004 p02(원본 TG 없음, seed 영역).
- 확인한 사실: `tg_splits`는 원본 TG를 바꾸지 않고 effective에만 쓴다. CV/Step 2 재실행은 원본을 다시 쓰지만, 같은 입력이면 같은 출력이라 digest가 그대로고 결과가 달라질 때만 바뀐다.

## 9. 남은 위험

1. **R2 — 충돌 접미사.** 같은 TG 집합 영역 둘(실데이터 0건)이 있을 때 `@n1` 접미사 참조가 보관됐다가 다른 영역으로 복귀할 수 있다. 낮음.
2. **R3 — 오래된 구 번들 탭.** 재분석 전에 열린 구 번들이 옛 R 번호로 직접 입력을 보내면 다른 미번역 영역에 붙을 수 있다(구 코드에도 있던 위험). 서버 재시작 뒤 탭 새로고침으로 회피.
3. **첫 재부여 미보호.** 배포 시점의 기존 수동값 snapshot에는 digest가 없다. 그 페이지에서 sync(재분석·OCR·적용)가 한 번 돌아야 `raw:` digest가 찍힌다. 그 전에 force 재분석으로 CV 결과가 달라지면 정확 일치로 다른 말풍선에 붙을 수 있다. 실데이터 overlay 키는 6개 파일 7개 정도라 노출이 작다.
4. **원본 TG 파일이 없는 페이지.** 앱과 렌더러 모두 digest가 None이라 보호가 꺼진다(둘이 같은 동작이라 어긋나지는 않음). `raw:`로 보관된 값은 파일이 없어진 뒤 정확 일치로 다른 말풍선에 돌아올 수 있다. 실데이터 164페이지 중 1개(00004_004_p02_L)이고 그곳은 `guide:` 참조라 digest와 무관하다.
5. **보관함 UI 없음.** `unmatched_regions`를 볼 화면이 없다. 보관된 값은 원래 참조가 다시 일치할 때만 복귀하며, merge_conflict·shrink 이동·legacy 번호 키 보관분은 자동 복귀하지 않는다(안전 쪽). 범위 밖으로 합의됨.
6. **단계 9 미진행.** `overlay_anchor.py` IoU remap 축소, 이중 기록 종료 여부는 사용자 결정으로 남아 있다. 이중 기록을 끝내면 overlay 롤백이 불가능해진다.
7. **기타 잔여.** 00011에서 사용자가 TG2를 다시 켜면 보관된 `tg:2`의 빈 text_override가 돌아와 번역이 숨겨진다(같은 대사의 수동값이라는 규칙상 일관되나 의도와 다를 수 있음). Edge 상자가 겹치면 `_derive_guide_ids`가 첫 상자를 고르므로 틀린 id가 강한 참조가 되어 제외가 무시될 수 있다(실데이터 미확인). `tg_splits` 새 TG id가 분할 순서 의존이라 앞 분할을 지우면 바뀐다(실사용 0건). 예전 effective 기준 digest로 보관된 값의 되돌림은 로컬 테스트 데이터에서만 가능성이 있다(맥미니 실데이터 0건).
8. **확인하지 못한 것.** 00011 사건의 실제 조작 순서(파일 내용·시각 추정), 00005·00009 어긋남의 원인, 다른 프로젝트 데이터, Gemini가 같은 영역을 매번 같은 세그먼트 수로 나누는지, 앱 서버·브라우저 실제 동작은 리뷰에서 직접 확인하지 않았다. 실데이터 대규모 렌더 비교도 하지 않았다(합성 fixture 위주).

## 10. 회귀 테스트

합성 JSON 기반(T1 사고 재현, T2 재번호, T3 자동→Edge 대체, T4 합침, T5 분할, T5b 축소, T6 seed, T7 자동 분할 자식, T8 재번역, T9 구파일 변환, T10 롤백, T11 API, T12 렌더러 번호 공간 혼용 4곳)과 프런트 proof(F1 번역 번호≠현재 번호에서 현재 영역 외곽선 선택, F2 재분석 뒤 선택 유지). 리뷰 시점의 공백은 지적 9와 v2 평가에 적었다: 구 projection → 렌더러 경로, 분석 탭 선택 유지는 fixture가 약하다.
