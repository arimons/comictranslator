<!-- OCR font_style enum별 실제 폰트 매핑 TODO -->

# Font Style TODO

OCR 프롬프트는 `font_style`을 제한된 enum으로 반환한다. 아직 실제 폰트 매핑은 임시이며, 아래 카테고리별 한국어 지원 폰트를 찾아야 한다.

## Font Style Categories

- `default`
  - 일반 인쇄체 또는 판단 불가.
  - 현재 기본 폰트 유지.

- `handwritten_round`
  - 둥글고 귀여운 손글씨.
  - 코미디, 당황, 귀여운 독백, 말랑한 감정 표현용 폰트 필요.

- `handwritten_rough`
  - 거칠고 불규칙한 손글씨.
  - 분노, 초조함, 절규, 거친 외침용 폰트 필요.

- `impact_bold`
  - 굵고 강한 강조체.
  - 위압감, 강한 대사, 큰 외침용 블랙/헤비 고딕 계열 폰트 필요.

- `serif_drama`
  - 명조/세리프 또는 날카로운 장식 획이 있는 극적 강조체.
  - 공포, 비극, 운명, 선언, 심각한 독백용 폰트 필요.
  - 00003 TG9 같은 케이스의 1차 후보.

- `sfx_brush`
  - 붓글씨 또는 효과음 전용 스타일.
  - 효과음, 타격음, 움직임 표현용 폰트 필요.

## Implementation Notes

- 현재 `pipeline/render_text_overlay.py`는 `font_style`을 읽지만, 실제 매핑은 임시로 `Pretendard-Black` 또는 `NanumBarunpenB`에 연결한다.
- 폰트를 추가하면 `font_path_for_cluster()`에서 enum별 경로를 명시적으로 매핑한다.
- `location == "unboxed"`이고 `font_type == "handwritten"`이면 원본 유지 정책을 우선한다.
- `location == "bubble"`이면 `handwritten`이어도 overlay 한다.
