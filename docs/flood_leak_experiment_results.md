# Flood Fill 탈출 방지 실험 결과 — 00158.jpg

## 테스트 대상

우상단 말풍선 (A1/A6 — `貴様らが緩み過ぎなだけだ` / `もし戦闘が始まれば真っ先に死ぬぞ`)
→ 말풍선 경계가 캐릭터 얼굴/패널 경계와 겹쳐서 **flood fill이 상단 패널 전체로 탈출**

---

## 결과 비교

### A1 — Baseline (기존: area_ratio = 0.1199, 탈출)

![A1 Baseline — flood가 상단 패널 전체로 퍼짐](/Users/changhyunpark/.gemini/antigravity/brain/ac7009eb-33d9-4a9c-9e3a-85354df079cc/artifacts/00158_A1_0_baseline.jpg)

---

### B — Convex Hull Clipping (area_ratio = 0.1199, ❌ 실패)

![A1 Convex Hull — baseline과 동일, 클리핑 실패](/Users/changhyunpark/.gemini/antigravity/brain/ac7009eb-33d9-4a9c-9e3a-85354df079cc/artifacts/00158_A1_B_convex_hull.jpg)

> [!WARNING]
> **Convex Hull 접근은 이 케이스에서 완전히 실패.** 탈출한 flood 영역이 하나의 거대한 connected component이므로, hull로 자르더라도 anchor 근처 영역과 탈출 영역이 같은 contour에 속해서 분리가 불가.

---

### C — Edge Barrier (area_ratio = 0.0408, ✅ 성공!)

````carousel
![A1 Edge Barrier — 말풍선 경계를 정확히 잡음 (area_ratio=0.041)](/Users/changhyunpark/.gemini/antigravity/brain/ac7009eb-33d9-4a9c-9e3a-85354df079cc/artifacts/00158_A1_C_edge_barrier.jpg)
<!-- slide -->
![A6 Edge Barrier — 같은 말풍선의 다른 anchor도 성공](/Users/changhyunpark/.gemini/antigravity/brain/ac7009eb-33d9-4a9c-9e3a-85354df079cc/artifacts/00158_A6_C_edge_barrier.jpg)
````

> [!TIP]
> **Edge Barrier가 가장 좋은 결과.** Canny 에지를 장벽으로 사용하여 캐릭터 얼굴의 윤곽선이 flood fill을 막아줌. 말풍선 내부만 정확하게 잡음.
> - `soft` (30/100): area_ratio = 0.040
> - `mid` (50/150): area_ratio = 0.040
> - `hard` (80/200): area_ratio = 0.041 ← 가장 넓게 (best)
> - 세 config 모두 성공. `anchor bbox 내부 에지 제거`가 핵심 트릭.

---

### D — Dual Tolerance (lo=4/up=5에서 성공, area_ratio = 0.0413, ✅ 성공!)

````carousel
![A1 D:strict — tolerance를 줄이자 탈출 방지됨](/Users/changhyunpark/.gemini/antigravity/brain/ac7009eb-33d9-4a9c-9e3a-85354df079cc/artifacts/00158_A1_D_strict_lo4up5.jpg)
<!-- slide -->
![A6 D:strict — 같은 tolerance 결과](/Users/changhyunpark/.gemini/antigravity/brain/ac7009eb-33d9-4a9c-9e3a-85354df079cc/artifacts/00158_A6_D_strict_lo4up5.jpg)
````

> [!NOTE]
> Tolerance 단계별 전환점:
> - `lo=8, up=10` (기존): 0.1199 ❌ 탈출
> - `lo=6, up=8`: 0.1196 ❌ 탈출 (거의 동일)
> - `lo=4, up=5`: **0.0413** ✅ 성공!
> - `lo=2, up=3`: 0.0411 ✅
> - `lo=1, up=1`: 0.0405 ✅

---

## 수치 요약

| 방법 | A1 area_ratio | A6 area_ratio | A10 area_ratio | 성공 여부 |
|------|:---:|:---:|:---:|:---:|
| Baseline (lo=8,up=10) | 0.1199 | 0.1199 | 0.0596 | ❌ A1/A6 탈출 |
| **B** Convex Hull | 0.1199 | 0.1199 | 0.0596 | ❌ 효과 없음 |
| **C** Edge Barrier | **0.0408** | **0.0408** | 0.0588 | ✅ |
| **D** Dual Tol (lo=4) | **0.0413** | **0.0413** | 0.0595 | ✅ |

---

## 결론 및 추천

> [!IMPORTANT]
> **B (Convex Hull)은 폐기.** 탈출이 큰 connected component로 퍼지면 hull 분리가 불가.

### 추천: C + D 조합 (Retry with Escalation)

```
1차: 기존 flood fill (lo=8, up=10)
  → area_ratio ≤ threshold → 성공, 종료

2차 (탈출 감지 시): Edge Barrier flood fill
  → Canny edge + dilate로 장벽 생성 후 retry
  → 성공하면 종료

3차 (Edge Barrier도 실패 시): Dual Tolerance
  → tolerance를 단계적으로 줄여가며 retry
  → (lo=6,up=8) → (lo=4,up=5) → (lo=2,up=3)
```

이 조합의 장점:
- 정상 말풍선은 1차에서 빠르게 처리 (기존과 동일)
- 탈출 시 C(에지 장벽)로 **정확한 말풍선 형태** 유지
- C도 실패하면 D(tolerance 축소)로 **보수적 fallback**
