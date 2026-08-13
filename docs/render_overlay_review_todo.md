<!-- 렌더링 이슈 점검 TODO를 기록하는 문서 -->
# Render Overlay Review TODO

## 원칙

- `unboxed`는 `unboxed_rect`를 기본 overlay rect로 쓴다.
- `bubble`은 먼저 bubble/split region을 확정하고, 그 region의 `safe_text_rect`를 기본 overlay rect로 쓴다.
- 큰 bubble 안에서 일부 TG/segment만 그릴 때는 시각적 완결성을 위해 TG 위치를 반영한 sub safe rect를 쓴다.
- `bubble_rect`와 `split_region`은 기본 overlay rect 후보가 아니라 `safe_text_rect` 산출 및 fallback의 재료다.
- 페이지 전체 font band cap은 폐기했다. 남은 font size 이슈는 `choose_layout()` 내부 탐색 상한과 후보 선택 문제로 본다.
- 세로쓰기 판정은 `render_hint` 단독이 아니라 CV 기반 `style_features`, 원문 구조, bbox aspect, layout fill을 함께 본다.

## 완료

- 00049 TG1.
  - CV상 초대형 짧은 제목형 텍스트를 serif 강조 폰트로 렌더한다.
  - 실제 세로 후보를 배치해보고 bbox 높이를 더 잘 쓰면 세로 layout을 채택한다.
  - 현재 결과는 `['「', '플', '레', '임', '컬', '럼', '」', '!']`, font 65.

- 00025 TG12.
  - 짧은 impact/효과문 계열도 세로 후보를 배치해보고 채택한다.
  - 현재 결과는 `['으', '와', '아']`, font 77.

- 00052 TG4.
  - 현재 font 56으로 렌더되며, 우선 해결된 것으로 본다.

## 재확인 중

- 00026 TG1/TG2.
  - split 후 각 split 안에서 텍스트가 감지되었으므로 split-safe rect에 그리는 방향은 원칙상 맞지만, 현재 R1 safe rect와 split 자체가 시각적으로 이상한지 재확인한다.
  - R1 `safe_text_rect [1089,247,59,66]`, `mask_centroid [1118,280]`, inner center가 원문 `そ` 위치와 맞는다.
  - R2 `safe_text_rect [1057,324,87,155]`, `mask_centroid [1100,401]`, inner center가 원문 `それでは…！` 위치와 맞는다.
  - safe rect 시각화가 너무 진한 magenta라 split line 판단을 방해했으므로 먼저 시각화 색상과 두께를 조정한다.
  - 다음 판단은 새 vis 기준으로 사용자와 함께 진행한다.

## 대기

- 00060 R13/R14.
  - 우상단 손글씨 아래 일본어 잔재가 남는다.
  - `anchor_ids == []`인 edge/flood bubble region의 마스킹/복원 정책 문제로 추정한다.
  - 원칙은 edge/flood로 잡힌 bubble이면 지우되, 정확한 TG bbox 없는 원본 복원은 제한한다.

- 00061 R1/R2.
  - R1은 `anchor_ids == []`, R2는 TG1이 두 segment로 중복 매핑된다.
  - 같은 TG가 한 region 안에서 여러 segment로 반복 매핑되는 integrated 생성 문제로 본다.

- 00069 R12.
  - `anchor_ids == []`인 split/bubble region이 남는다.
  - 00060과 같은 anchor 없는 region 정책으로 묶어 처리한다.

- 00059, 00061, 00065, 00069 OCR region 미스매치.
  - chunking은 동작하지만, `matched_cluster == []`, anchor 없는 region, 같은 TG 중복 segment를 검증하지 못한다.
  - integrated 생성 단계에서 OCR 원문과 anchor 원문 유사도, 빈 matched cluster, 중복 TG를 검증해야 한다.

- 남은 font 탐색 상한.
  - `choose_layout()` 내부 `estimated_max` 상한이 아직 실제 cap처럼 작동할 수 있다.
  - 페이지 cap 제거 이후 별도 점검한다.
