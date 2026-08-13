<!-- 캐릭터나 손글씨가 말풍선 경계를 열거나 침범하는 어려운 사례를 축적하는 문서 -->

# Open Bubble Boundary Text Cases

이 폴더는 캐릭터, 손글씨, 아이콘, 효과선 등이 말풍선 contour를 열거나 침범해서 일반 bubble 판정과 텍스트 그룹 분리가 애매해지는 사례를 모은다.

목표는 즉시 해결이 아니라, 충분한 사례를 모은 뒤 한 번에 적용 가능한 규칙이나 후처리 전략을 검토하는 것이다.

## 분류 상태

- `candidate`는 사람이 아직 최종 분류를 확정하지 않은 사례다.
- `confirmed`는 이 유형으로 확정한 사례다.
- `rejected`는 비슷해 보였지만 다른 문제로 분류한 사례다.

## 사례 목록

### 007_p02_L R8

- 상태: `candidate`
- 유형 후보: `boundary_handwriting`, `open_bubble_by_attached_text`
- 원본: [007_p02_L_input.jpg](assets/007_p02_L_input.jpg)
- 현재 시각화: [007_p02_L_bubble_vis.jpg](assets/007_p02_L_bubble_vis.jpg)
- 현재 proof 입력: `proof/pre_ocr_v03_split_smoke/split_input/007_p02_L.jpg`
- 현재 region JSON: `proof/pre_ocr_v03_split_smoke/json/007_p02_L_regions.json`
- 현재 text group JSON: `proof/pre_ocr_v03_split_smoke/json/007_p02_L_text_groups.json`
- 관찰 대상 region: `R8`
- 관련 TG: `TG6`, `TG7`, `TG8`, `TG9`

현재 산출물 기준으로 `R8`은 bubble로 살아남고, anchor set은 `[6, 7, 8, 9]`로 묶인다. `TG6`은 본대사 후보이고, `TG7`, `TG8`, `TG9`는 말풍선 왼쪽 경계에 붙은 손글씨 또는 경계 침범 텍스트 후보로 보인다.

확정하지 않은 관찰 포인트.

- bubble 자체는 split 이후 유지된다.
- 경계 손글씨 후보가 bubble anchor set에 같이 묶인다.
- 이런 유형은 배경이나 contour 조건이 달라지면 edge-barrier 계열 실패로 바뀔 가능성이 있다.
- 해결 후보는 bubble 판정 자체보다 bubble 내부 텍스트와 boundary-attached text를 분리하는 후처리일 수 있다.

## 나중에 검토할 아이디어

- eroded bubble mask 내부에 안정적으로 포함되는 TG와 contour 근처 band에 붙은 TG를 분리한다.
- safe text rect 포함 비율, distance transform, contour proximity를 함께 본다.
- 짧은 손글씨 TG가 경계 방향으로 연속되는 패턴을 보조 지표로 둔다.
- bubble 판정은 내부 본대사 기준으로 유지하고, boundary text는 별도 타입으로 OCR sheet에 싣는 방식을 검토한다.

