# BFS 클러스터링 완벽 가이드

**작성일**: 2025-12-29
**대상**: cluster_cv_v5.py의 2-Pass BFS 알고리즘
**개념**: 세로쓰기 만화 텍스트를 단계적으로 그룹화하기

---

## 📚 목차

1. [개요 및 개념](#개요-및-개념)
2. [Pass 1: Block → Line](#pass-1-block--line)
3. [후리가나 필터링](#후리가나-필터링)
4. [Pass 2: Line → Cluster](#pass-2-line--cluster)
5. [실제 동작 예시](#실제-동작-예시)
6. [매개변수 설정](#매개변수-설정)

---

## 개요 및 개념

### 왜 BFS 클러스터링이 필요한가?

**문제 상황**

Cloud Vision API가 만화 이미지를 분석하면:

```
원문: "「こんにちは」"

Cloud Vision이 감지한 결과:
┌─┐  ┌─┐  ┌─┐  ┌─┐  ┌─┐  ┌─┐  ┌─┐
│「│  │こ│  │ん│  │に│  │ち│  │は│  │」│
└─┘  └─┘  └─┘  └─┘  └─┘  └─┘  └─┘
 ↑    ↑    ↑    ↑    ↑    ↑    ↑
개별 박스... 모두 분리되어 있음
```

개별 글자 박스들을 다시 "의미있는 단위"로 묶어야 합니다:
- **라인(Line)**: 한 세로줄의 모든 글자
- **클러스터(Cluster)**: 한 대사/나레이션의 모든 세로줄

### BFS란?

**BFS (너비-우선 탐색, Breadth-First Search)**

> "한 지점에서 시작해서, 같은 거리의 모든 노드를 먼저 방문한 후, 그 다음 거리로 나아가는 그래프 탐색 알고리즘"

**일상적 비유**: 연못에 돌을 던졌을 때 물결이 퍼져나가는 모습

```
      👤 (시작점)
      |
   ❶ ❶ ❶  (1단계 거리)
   ❶ 👤 ❶
   ❶ ❶ ❶
      |
   ❷ ❷ ❷ (2단계 거리)
   ❷ ❷ ❷
   ...
```

모든 1단계 거리의 노드를 처리한 후, 2단계로 나아갑니다.

### 우리의 2-Pass 파이프라인

```
┌──────────────────────────────────────┐
│   입력: Cloud Vision JSON             │
│   (개별 텍스트 박스들)                │
└──────────────┬───────────────────────┘
               │
        ┌──────▼──────┐
        │ Pass 1 BFS   │
        │ Block→Line   │
        └──────┬───────┘
               │ (같은 X좌표, 인접 Y)
               │
        ┌──────▼──────────┐
        │ 후리가나 필터링   │
        │ (작은 글자 제거)  │
        └──────┬───────────┘
               │
        ┌──────▼───────┐
        │ Pass 2 BFS    │
        │ Line→Cluster  │
        └──────┬────────┘
               │ (4-stage 규칙)
               │
        ┌──────▼──────────────┐
        │ 출력: Cluster JSON   │
        │ (대사/나레이션 박스) │
        └───────────────────────┘
```

---

## Pass 1: Block → Line

### 목표

Cloud Vision이 감지한 개별 글자 박스들(**Block**)을 한 세로줄 단위로 묶어서 **Line**을 만듭니다.

### 개념

세로쓰기 만화의 특징:

```
같은 세로줄:        다른 세로줄:
「(x=100)          と(x=50)
こ(x=100)          言(x=50)
ん(x=100)          っ(x=50)
に(x=100)          た(x=50)
ち(x=100)
は(x=100)
」(x=100)

X 좌표가 거의 같음  X 좌표가 다름
Y 좌표만 증가       별개의 라인
```

### BFS 알고리즘 (코드: lines 72-118)

```python
def pass1_create_lines(blocks: List[Dict], avg_width, y_tolerance):
    """
    같은 X 그룹에서 Y 축으로만 블록을 병합

    조건: X diff ≤ 10px AND Y gap ≤ y_tolerance
    """
    blocks_sorted = sorted(blocks, key=lambda b: -b["bbox"][0])
    # 우→좌 읽기 (일본 만화 스타일)

    visited = set()
    lines = []

    for seed_block in blocks_sorted:
        if seed_block in visited:
            continue

        # BFS 초기화
        queue = [seed_block]
        current_line = [seed_block]
        visited.add(seed_block)

        # BFS 루프
        while queue:
            curr = queue.pop(0)  # FIFO: 먼저 들어온 것부터 처리
            cx, cy, cw, ch = curr["bbox"]

            # 모든 미방문 블록과 비교
            for candidate in blocks_sorted:
                if candidate in visited:
                    continue

                x, y, w, h = candidate["bbox"]
                x_diff = abs(x - cx)
                y_gap = max(0, y - (cy + ch), cy - (y + h))

                # Stage 1 ONLY: 같은 세로줄인가?
                if x_diff <= 10 and y_gap <= y_tolerance:
                    current_line.append(candidate)
                    queue.append(candidate)
                    visited.add(candidate)

        lines.append(create_line_object(current_line))

    return lines
```

### 핵심 개념: FIFO (First In, First Out)

```python
queue.pop(0)  # 맨 앞에서 꺼냄
```

이것이 "너비-우선"을 만듭니다:

```
Step 1: queue = [Block 0]
        └─ Block 0 처리 → 조건 맞는 모든 블록 추가
           queue = [Block 1, Block 2, Block 5]

Step 2: queue = [Block 1, Block 2, Block 5]
        └─ Block 1 처리 (가장 먼저 들어온 것)
           queue = [Block 2, Block 5, Block 3, Block 4]

Step 3: queue = [Block 2, Block 5, Block 3, Block 4]
        └─ Block 2 처리
        ...
```

비유: "먼저 온 사람부터 순서대로 처리하기"

### 출력: Line 객체

```python
{
    "bboxes": [List of original blocks],
    "bbox": [x, y, w, h],           # Line의 전체 경계
    "text": "こんにちは",            # 텍스트 연결
    "orientation": "vertical"        # 세로/가로 판정
}
```

---

## 후리가나 필터링

### 목표

일본어의 작은 읽음 주석을 제거합니다.

```
원문:               후리가나:
こんにちは    →    こ   ん  に  ち  は
(큰 글자)          (작은 글자들)
```

만화에서는 후리가나가 따로 감지될 수 있으므로 제거해야 합니다.

### 개념

```
후리가나의 특징:

1. "바로 왼쪽" Line의 Y 범위와 겹침
   (세로쓰기에서 "왼쪽"은 X가 더 작은 것)

2. 너비가 왼쪽 Line의 60% 이하
   (작은 글자들이므로 당연히 좁음)

3. X gap은 무시 (인접 여부만)
```

### 알고리즘 (코드: lines 177-224)

```python
def filter_furigana_lines(lines: List[Dict], avg_width):
    """
    Line 레벨에서 후리가나 제거

    조건:
    1. 바로 왼쪽 Line과 Y 범위 겹침 (overlap)
    2. Width ≤ 왼쪽 Line의 60%
    """
    lines_sorted = sorted(lines, key=lambda l: -l["bbox"][0])

    filtered = []
    for i, line in enumerate(lines_sorted):
        lx, ly, lw, lh = line["bbox"]
        is_furigana = False

        # 바로 왼쪽 Line 찾기 (X가 더 작은 것들)
        for j in range(i + 1, len(lines_sorted)):
            left = lines_sorted[j]
            left_x, left_y, left_w, left_h = left["bbox"]

            # Y 범위 겹침 체크 (±50px 허용)
            y_overlap = not (
                line_y_end < left_y - 50 or
                ly > left_y_end + 50
            )

            if y_overlap:
                # Width 체크: 60% 이하?
                if lw <= left_w * 0.60:
                    print(f"✂️ Furigana removed: '{line['text']}'")
                    is_furigana = True
                    break

            # 너무 멀면 중단 (X 좌표 차이 > 100px)
            if left_x < lx - 100:
                break

        if not is_furigana:
            filtered.append(line)

    return filtered
```

### 시각적 예시

```
입력 (3개 Line):
Line 1: "こんにちは" (x=100, y=10~100, w=100)  ← 큰 글자
Line 2: "こ"           (x=105, y=15~25, w=20)   ← 작은 글자, Y 겹침, w=20%
Line 3: "は"           (x=50, y=20~50, w=150)   ← 다른 라인

처리:
Line 2를 Line 1과 비교
  - Y 겹침? YES (y=15~25는 y=10~100에 포함)
  - w <= 60%? YES (20 ≤ 60)
  → 후리가나! 제거 ✂️

처리:
Line 3를 처리
  - Line 1과 Y 겹침? YES
  - w <= 60%? NO (150 > 60)
  → 후리가나 아님, 유지

출력 (2개 Line):
Line 1: "こんにちは"
Line 3: "は"
```

---

## Pass 2: Line → Cluster

### 목표

필터링된 Line들을 **4-Stage 규칙**으로 하나의 대사 박스(**Cluster**)로 묶습니다.

### 개념

만화의 한 대사는 여러 세로줄에 걸쳐 있을 수 있습니다:

```
한 대사의 여러 세로줄:

Line 1          Line 2          Line 3
「(x=100)       と(x=50)        そ(x=0)
こ(x=100)       言(x=50)        れ(x=0)
ん(x=100)       っ(x=50)        は(x=0)
に(x=100)       た(x=50)        」(x=0)
ち(x=100)
は(x=100)
」(x=100)

모두 같은 "대사"를 표현하지만,
X 좌표가 다름 (100 → 50 → 0)
```

Pass 2는 이들을 4-Stage 규칙으로 연결합니다.

### 4-Stage 규칙 (코드: lines 231-306)

```python
def pass2_create_clusters(lines, avg_width, y_tolerance):
    """
    Line들을 4-stage 규칙으로 클러스터링
    """
    dynamic_threshold = avg_width * 2.5

    lines_sorted = sorted(lines, key=lambda l: -l["bbox"][0])

    visited = set()
    clusters = []

    for seed_line in lines_sorted:
        if seed_line in visited:
            continue

        queue = [seed_line]
        current_cluster = [seed_line]
        visited.add(seed_line)

        while queue:
            curr = queue.pop(0)
            cx, cy, cw, ch = curr["bbox"]

            for candidate in lines_sorted:
                if candidate in visited:
                    continue

                lx, ly, lw, lh = candidate["bbox"]
                x_diff = abs(lx - cx)
                y_gap = max(0, ly - (cy + ch), cy - (ly + lh))
                y_start_diff = abs(ly - cy)

                # ===== Stage 1: 같은 세로줄 =====
                if x_diff <= 10:
                    # 특수문자 뒤면 완화
                    effective_tolerance = y_tolerance
                    if curr_text.endswith(('…', '!', '？', '。')):
                        effective_tolerance = y_tolerance * 1.5

                    if y_gap <= effective_tolerance:
                        merge(candidate)

                # ===== Stage 2: 약간 벌어진 라인 =====
                elif 10 < x_diff <= 55:
                    # Y 시작점이 비슷하면 병합
                    if y_start_diff <= y_tolerance:
                        merge(candidate)

                # ===== Stage 3: 더 벌어진 라인 =====
                elif 55 < x_diff <= dynamic_threshold:
                    # Y 시작점이 비슷하면 병합
                    if y_start_diff <= y_tolerance:
                        merge(candidate)

                # ===== Stage 4: 너무 멀면 분리 =====
                else:
                    # x_diff > dynamic_threshold
                    # 병합하지 않음
                    pass

        clusters.append(current_cluster)

    return clusters
```

### Stage별 상세 설명

#### Stage 1: X diff ≤ 10px (같은 세로줄)

```
조건: X 차이가 10픽셀 이내

용도: Pass 1에서 놓친 블록들을 같은 라인에 묶기
     (예: Line 병합 후 발견된 인접 글자)

상세:
  - Y 간격 ≤ y_tolerance 면 병합
  - ✨ 특수문자('…', '!', '。') 뒤면 1.5배 완화
    (이유: CV 감지 오류 보정)

예시:
  Line A: "何…" (x=100)
  Line B: "か" (x=105, y_gap=40px)

  일반적으로: y_gap(40) > y_tolerance(29) → 병합 X
  특수문자 뒤: y_gap(40) ≤ 1.5×29(43.5) → 병합 O ✓
```

#### Stage 2: 10 < X diff ≤ 55px (인접한 라인들)

```
조건: X 차이가 10~55픽셀

용도: 같은 높이에 놓인 2~3개의 인접 라인 연결

판정: Y 시작점 유사성만 확인
      (y_gap은 무시, y_start_diff로 판정)

예시:
  Line A: "「こんに" (x=100, y=10~60)
  Line B: "ちは」"   (x=45,  y=15~65)

  X 차이: 55 ✓
  Y 시작: |15-10|=5 ≤ y_tolerance(29) ✓
  → 병합! (같은 높이에서 약간 옆)
```

#### Stage 3: 55 < X diff ≤ dynamic_threshold (멀리 떨어진 라인들)

```
조건: X 차이가 55~(avg_width×2.5) 픽셀

용도: 더 멀리 떨어진 라인도 연결 (격자 패턴)

판정: Stage 2와 동일 (Y 시작점 유사성)

예시:
  Line A: "「" (x=100, y=10)
  Line B: "と" (x=40, y=15)
  Line C: "そ" (x=-20, y=10)

  A→B: X diff=60, 10 < 60 ≤ 55? NO → Stage 3
       55 < 60 ≤ dynamic_threshold? 체크...
       Y start diff=5 ≤ 29? YES → 병합 O
```

#### Stage 4: X diff > dynamic_threshold (분리)

```
조건: X 차이가 너무 큼

판정: 병합하지 않음 → 다른 클러스터로 분류

이유: 다른 대사일 가능성이 높음
```

### ✨ 특수문자 완화 로직

```python
# Line이 특수문자로 끝나면?
if curr_text.endswith(('…', '!', '？', '。', 'っ', 'た')):
    effective_tolerance = y_tolerance * 1.5
```

**이유**: Cloud Vision의 감지 오류 보정

```
원문: "何…"
Cloud Vision 감지:
何(정확한 위치)
…(위치가 조금 높음) ← CV 오류

일반 y_tolerance로는 연결 실패
→ 특수문자 뒤면 1.5배 완화 → 연결 성공 ✓
```

---

## 실제 동작 예시

### 입력: 7개 Block

```json
[
  {"text": "「", "bbox": [100, 10, 15, 20]},
  {"text": "こ", "bbox": [100, 35, 15, 20]},
  {"text": "ん", "bbox": [100, 60, 15, 20]},
  {"text": "に", "bbox": [95, 85, 15, 20]},
  {"text": "ち", "bbox": [100, 110, 15, 20]},
  {"text": "は", "bbox": [50, 20, 15, 20]},
  {"text": "」", "bbox": [50, 50, 15, 20]}
]
```

### Pass 1 실행 (Block → Line)

```
Step 1: Block 0으로 BFS 시작
  queue = [Block 0]
  current_line = [Block 0]

Step 2: Block 0 처리
  Block 1: x_diff=0 ✓, y_gap=5 ✓ → 추가
  Block 5: x_diff=50 ✗ → 스킵

  queue = [Block 1]

Step 3: Block 1 처리
  Block 2: x_diff=0 ✓, y_gap=5 ✓ → 추가
  Block 3: x_diff=5 ✓, y_gap=30 ✗ → 스킵 (너무 멀음)

  queue = [Block 2]

Step 4: Block 2 처리
  Block 3: x_diff=5 ✓, y_gap=5 ✓ → 추가

  queue = [Block 3]

Step 5: Block 3 처리
  Block 4: x_diff=5 ✓, y_gap=5 ✓ → 추가

  queue = [Block 4]

Step 6: Block 4 처리
  (Block 5, 6은 X 차이 > 10 → 스킵)

결과 Line 1:
  blocks: [0, 1, 2, 3, 4]
  text: "「こんにちは"
  bbox: [95, 10, 30, 120]

Step 7: Block 5로 새 라인
  queue = [Block 5]
  Block 6: x_diff=0 ✓, y_gap=10 ✓ → 추가

  queue = [Block 6]
  (모두 처리됨)

결과 Line 2:
  blocks: [5, 6]
  text: "は」"
  bbox: [50, 20, 15, 50]
```

### 후리가나 필터링 (변화 없음)

이 예시에는 후리가나가 없으므로 2개 Line 모두 유지됩니다.

### Pass 2 실행 (Line → Cluster)

```
입력: 2개 Line
Line 1: "「こんにちは" (x=95, y=10~120)
Line 2: "は」"        (x=50, y=20~70)

Step 1: Line 1로 BFS 시작
  queue = [Line 1]
  current_cluster = [Line 1]

Step 2: Line 1 처리
  Line 2: x_diff=45
    - Stage 1? (0 < 45 ≤ 10)? NO
    - Stage 2? (10 < 45 ≤ 55)? YES ✓
      y_start_diff = |20-10| = 10 ≤ 29? YES ✓
      → 추가!

  queue = [Line 2]
  current_cluster = [Line 1, Line 2]

Step 3: Line 2 처리
  (모든 다른 라인 이미 처리됨)

결과 Cluster 1:
  cluster_id: 1
  lines: [Line 1, Line 2]
  text: "「こんにちは" + "は」"
  bounding_rect: [50, 10, 80, 110]
```

### 최종 출력

```json
[
  {
    "cluster_id": 1,
    "orientation": "vertical",
    "lines": [
      {
        "text": "「こんにちは",
        "bbox": [95, 10, 30, 120]
      },
      {
        "text": "は」",
        "bbox": [50, 20, 15, 50]
      }
    ],
    "bounding_rect": [50, 10, 80, 110]
  }
]
```

---

## 매개변수 설정

### 자동 계산 (코드: lines 504-514)

```python
def process_cv_clusters(cv_path, output_path,
                        image_width=1350, image_height=1920):

    # 1. Block 너비 분포 분석
    widths = [b['bbox'][2] for b in blocks]

    # 2. 75 백분위수 (중상위 블록들의 너비)
    avg_width = np.percentile(widths, 75)
    # 일반적: 30~50px

    # 3. Y 축 허용 간격 (이미지 높이의 1.5%)
    y_tolerance = image_height * 0.015
    # 1920px 이미지 → 29px

    # 4. 동적 임계값 (Line→Cluster 단계)
    dynamic_threshold = avg_width * 2.5
    # avg_width=40 → 100px
```

### 파라미터 의미

| 파라미터 | 값 | 의미 |
|---------|-----|------|
| `avg_width` | 30-50px | 일반 글자의 너비 |
| `y_tolerance` | ~29px | 같은 라인 판정 기준 |
| `dynamic_threshold` | avg_width × 2.5 | 클러스터 최대 거리 |

### 조정 가이드

```
문제: 너무 많은 라인이 한 클러스터로 묶임
해결: y_tolerance 감소
      dynamic_threshold 감소

문제: 같은 대사가 여러 클러스터로 분리됨
해결: y_tolerance 증가
      dynamic_threshold 증가

문제: 후리가나가 안 제거됨
해결: 60% 임계값을 더 낮춤 (예: 50%)
```

---

## 요약: BFS 클러스터링의 흐름

```
┌─────────────────────────────────────────┐
│ 1. Block들 입력 (개별 글자 박스)        │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│ 2. Pass 1 BFS (Block → Line)            │
│    ├─ 같은 X 좌표 그룹 찾기              │
│    └─ Y 축으로 인접한 것들 병합          │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│ 3. 후리가나 필터링 (작은 글자 제거)      │
│    ├─ Y 범위 겹침 확인                   │
│    └─ 너비 60% 이하면 제거               │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│ 4. Pass 2 BFS (Line → Cluster)          │
│    ├─ Stage 1: X≤10 (같은 줄)           │
│    ├─ Stage 2: 10<X≤55 (인접)           │
│    ├─ Stage 3: 55<X≤동적값 (멀이)       │
│    └─ Stage 4: X>동적값 (분리)          │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│ 5. Cluster JSON 출력                     │
│    ├─ cluster_id                        │
│    ├─ lines (병합된 라인들)              │
│    └─ bounding_rect (전체 범위)         │
└─────────────────────────────────────────┘
```

---

## 핵심 통찰

| 개념 | 이유 |
|------|------|
| **2-Pass 설계** | Pass 1은 순수(X축만), Pass 2는 지능형(4-stage) |
| **BFS 선택** | 세로쓰기 구조에서 같은 깊이 요소를 동시 처리 |
| **FIFO (queue)** | 블록의 입력 순서 무관 → 안정적인 결과 |
| **후리가나 필터링** | Line 레벨에서 한 번에 처리 → 효율적 |
| **Stage 규칙** | 거리에 따라 병합 강도 조절 → 세밀한 제어 |
| **특수문자 완화** | CV 오류 보정 → 실무 robustness |
| **동적 임계값** | 이미지별 글자 크기 자동 적응 |

