# TextGroup Builder 변경 이력

## 현재 정식 버전

`pipeline/text_group_builder.py`
- `build_text_groups_v2()` — 공개 API
- `build_text_groups()` — 하위 호환 alias

---

## 2026-05-20 (2) — 파이프라인 정식 승격 및 버그 수정

### 파이프라인 통합
- `pipeline/cluster_cv_v5.py` → `archive/` 이동
- `pipeline_runner.py` Step 2: subprocess(`cluster_cv_v5.py`) → `build_text_groups_v2` 직접 호출
- 출력 파일명: `*_cv_clusters_v5.json` → `*_text_groups.json`
- `CLAUDE.md`, `AGENTS.md`, `docs/CODEBASE_FILE_MAP.md` 참조 업데이트

### 버그 수정 3종

**① Repair cross-bubble 오병합 방지 (page 00027)**

`repair_line_fragments`의 `merge_gap`을 전역 `median_h * 3`에서 버킷별 per-char 높이 기반으로 변경.
CV가 다글자 묶음을 반환해 `median_h`가 과대추정되는 문제 해결.

```
수정 전: merge_gap = max(y_tol*3, median_h*3)  → 172.5px (전역)
수정 후: bucket_merge_gap = min(per_char_h*3, merge_gap)  → ≈90px (버킷별)
```

결과: TG6(`なんであいつが`) / TG7(`まだ原作もスタートしていないこの段階で…`) 말풍선 경계 올바르게 분리

**② Guard B overlap 체크 기준 변경 (page 00051, 00076 보존)**

`consolidate_clusters` Guard B(좁은 1열 독립 말풍선 오병합 방지)의 containment 체크를
`compact_y_range` 기준 → **full bbox** 기준으로 변경.

compact_y_range가 tall line을 제외해 범위가 축소되면 실제로는 포함 관계인 마지막 열을
잘못 차단하는 문제(00051 TG9+TG10) 해결. 00076 케이스 여전히 정상 차단 확인.

**③ Consolidate 멀티패스 + 폭 상한 완화 (page 00107)**

- 단일 패스 한계: 앞 seed가 j를 스캔할 때 아직 병합되지 않은 클러스터와 비교 → 2패스 이후 조건 충족되도 기회 없음
  → 변화가 없을 때까지 최대 3회 반복 (`for _pass in range(3): ... if no change: break`)
- 병합 후 폭 상한: `avg_width * 7` → `avg_width * 8` (5열 말풍선의 x스팬이 7.5배에서 걸리는 케이스 허용)

결과: TG8(`その恩に`) + TG12(`るためなら自分の心を殺す覚悟などとうに決まっております`) 정상 병합

---

## v2 (2026-05-20) — 정식 승격 (초기)

**파일**: `pipeline/text_group_builder_v2.py` (`archive/experiment/src/text_group_builder_v2.py`에서 승격)

### 추가된 기능

**Pass 1.5 Line Fragment Repair** (`repair_line_fragments`)
- Pass1 이후 같은 세로열 내 끊어진 line 조각을 재병합
- CV가 한 세로줄의 글자를 여러 box로 분리 감지하는 현상 보완
- guard: y간격 초과 / 폭 비율 / 병합후 폭 과대 / 개입 line 존재

**Pass 3 Cluster Consolidation** (`consolidate_clusters`)
- Pass2 BFS에서 분리된 같은 말풍선 클러스터를 보수적으로 재병합
- Guard A: 좁은 클러스터 + 비인접 거리 → 다른 말풍선 (00037 계열)
- Guard B: 1열 + 2열 이상 + 양쪽 키 큼 + 범위 밖 삐져나옴 → 다른 말풍선 (00076 계열)
- y overlap ≥ 50% 조건으로 수직 범위 무관한 오병합 차단

### 해상도 정규화

하드코딩 픽셀값을 비율 기반으로 교체 (다해상도 / 2페이지 스프레드 대응)

| 항목 | 변경 전 | 변경 후 |
|---|---|---|
| Pass1/Pass2 x_diff (같은 열) | `<= 10px` | `<= avg_width * 0.33` |
| Pass2 x_diff (인접 열) | `<= 55px` | `<= avg_width * 2.0` |
| Repair x_tol | `10.0px` | `avg_width * 0.33` |
| Furigana y_tol | `50px` | `image_height * 0.026` |
| Furigana 탐색 한계 | `lx - 100px` | `lx - avg_width * 4.0` |
| _merge_adjacent x_diff | `<= 10px` | `<= avg_width * 0.33` |

### 검증

- 165페이지 (1350×1920) 전체 회귀: 오병합 증가 없음
- 00037 +1 (후리가나 독립 TG) — 알려진 케이스, 허용
- 2페이지 스프레드 960×1080 분할 이미지 동작 확인

---

## v1 (초기) — 레거시

**파일**: `experiment/src/text_group_builder.py` (비교 참조용으로 보존)

- Pass1 (blocks → lines) + Furigana 필터 + Pass2 BFS (lines → clusters)
- 고정 픽셀 임계값 (1350×1920 전용)
- `run_tg_vis.py`의 OLD 비교 사이드에서 계속 사용
