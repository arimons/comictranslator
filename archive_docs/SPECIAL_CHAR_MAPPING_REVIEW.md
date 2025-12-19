# Special Character Mapping 검토 이력

## 개요

CV (Computer Vision) 클러스터와 Gemini OCR 결과 매칭 시 **특수 문자 오인식** 케이스를 처리하기 위한 아이디어를 검토했습니다.

**상태**: 검토 완료, 코드 미반영 (기능 필요성 낮음)

---

## 배경

### 발견된 문제
- **00012.jpg Sentence 1**: Gemini가 정확히 인식한 `…!!` 를 CV가 잘못 감지 (예: `=:`)
- CV의 특정 문자 오인식 패턴 (예: `!!` ↔ `=:`)이 텍스트 레벨 매칭 알고리즘으로는 감지 불가능

### 기존 알고리즘의 한계
```
LCS ("…!!", "=:"):        0.0  ❌ (완전히 다른 문자)
Levenshtein ("…!!", "=:"): 0.0  ❌ (편집거리 3)
Cosine ("…!!", "=:"):      0.0  ❌ (공통 문자 없음)
```

---

## 검토한 솔루션

### Step 4: 특수 문자 매핑 (Rescue Logic)

**개념**:
- Step 1-3 (LCS, Cluster Combination, Levenshtein)에서 매칭 실패한 경우만
- 미매칭된 Sentence와 미매칭된 Cluster의 **조합에 대해서만** 특수 문자 매핑 적용
- 안전성: 이미 매칭된 항목에는 영향 없음

**구현**:
```python
SPECIAL_CHAR_MAPPING = {
    ("!!", "=:"): True,   # CV가 "!!"를 "=:"로 오인식
}

def check_special_char_mapping(text1: str, text2: str) -> Optional[float]:
    """CV 오인식 패턴 정확 매칭 (O(1) 해시 조회)"""
    # 공백 제거 후 매핑 확인
    if (text1, text2) in SPECIAL_CHAR_MAPPING:
        return 1.0
    return None
```

**적용 단계**:
1. Step 1-3으로 대부분의 문장 매칭 ✅
2. 미매칭 Sentence 수집
3. 미매칭 Cluster 수집
4. 양쪽 미매칭 조합에만 special_char_mapping 적용
5. 매칭되면 업데이트, 아니면 미매칭 유지

---

## 테스트 결과

### 배치 테스트 (4개 이미지)

| 이미지 | 결과 | 분석 |
|--------|------|------|
| **00010** | 2/5 (40%) | 특수 문자만 있는 문장들 (`…っ`, `……`) |
| **00012** | 4/5 (80%) | S1 '…!!' 미매칭 (특수 문자만) |
| **00013** | 6/6 (100%) | 완벽 매칭 |
| **00015** | 3/4 (75%) | `……っ` 미매칭 (특수 문자만) |
| **전체** | **15/20 (75%)** | 안정적 |

### 핵심 발견

✅ **Step 1-3만으로도 충분한 성능**
- 실질적으로 필요한 텍스트 매칭: 75%+ 성공
- 특수 문자만 있는 경우(`…!!`, `……`, `…っ`)는 불가피하게 미매칭

❌ **Step 4의 낮은 필요성**
- S1 ('…!!') vs C7 ('=:'): `check_special_char_mapping("…!!", "=:")` → None
  - 매핑이 `("!!", "=:")`이므로 정확히 매칭되지 않음
  - 구조적으로 특수 문자 전용 Sentence는 처리 불가능

🎯 **사용자 인사이트** (결정적)
> "근데 굳이 생각해보니 문자가 아닌 케이스는 그냥 누락해도 상관없겠다 싶긴하네. 저걸 어짜피 지우고 Overlay 하는게 의미가 없을거 같아."

- 특수 문자(`…!!`, `!!!`, `…` 등)는 Overlay 렌더링 단계에서 **삭제됨**
- 따라서 이런 케이스를 매칭시킬 필요가 없음

---

## 최종 판단: 코드 미반영

### 이유

1. **필요성 낮음**
   - 특수 문자만 있는 Sentence는 Overlay에서 지워짐
   - 실질적으로 해석 필요 없음

2. **복잡성 vs 이득**
   - Step 4 추가 로직: ~40줄
   - 이득: 특수 문자 Sentence 매칭 (거의 사용 안 함)

3. **구조적 한계**
   - `("…!!", "=:")`처럼 특수 문자를 포함한 매핑은 필요성 불명확
   - `("!!", "=:")`만으로는 S1을 해결할 수 없음

4. **향후 확장성**
   - 필요시 언제든 추가 가능
   - 지금 추가해도 사용되지 않을 것으로 예상

---

## 결론

| 항목 | 평가 |
|------|------|
| **아이디어 타당성** | ✅ 논리적으로 타당 |
| **구현 안정성** | ✅ 안전하게 구현됨 |
| **필요성** | ❌ 낮음 (특수 문자는 삭제됨) |
| **비용/이득** | ❌ 복잡성 대비 이득 부족 |
| **권장사항** | ❌ 코드 미반영 |

**최종 상태**:
- ✅ 검토 및 테스트 완료
- 🗑️ 코드에서 제거
- 📋 이 문서에 이력 기록

---

## 참고: 구현했던 코드

아래는 실제 구현했던 코드입니다. (현재는 `integrate_ocr_v3.py`에서 제거됨)

```python
# Special Character Mapping (CV 오류 패턴)
SPECIAL_CHAR_MAPPING = {
    ("!!", "=:"): True,   # "!!"를 CV가 "=:"로 오인식
}

def check_special_char_mapping(text1: str, text2: str) -> Optional[float]:
    """특수 문자 매핑 확인 (O(1) 해시 조회)"""
    t1 = text1.replace('\n', '').replace(' ', '').replace('　', '')
    t2 = text2.replace('\n', '').replace(' ', '').replace('　', '')

    if (t1, t2) in SPECIAL_CHAR_MAPPING or (t2, t1) in SPECIAL_CHAR_MAPPING:
        return 1.0
    return None

# Step 4 로직 (integrate_ocr_v3 함수 내)
unmatched_sentences = [item for item in integrated if item['matched_cluster'] is None]

if unmatched_sentences:
    matched_cluster_ids = {item['matched_cluster'] for item in integrated if item['matched_cluster'] is not None}
    unmatched_clusters = [c for c in cv_clusters if c['cluster_id'] not in matched_cluster_ids]

    for sent_item in unmatched_sentences:
        sent_text = sent_item['text']
        for uc in unmatched_clusters:
            uc_full_text = ''.join([line['text'] for line in uc.get('lines', [])]).replace('\n', '').replace(' ', '').replace('　', '')

            special_score = check_special_char_mapping(sent_text, uc_full_text)
            if special_score is not None:
                # 매칭 업데이트
                sent_item['matched_cluster'] = uc['cluster_id']
                # ...
                break
```

---

## 관련 파일

- `integrate_ocr_v3.py`: Step 1-3 구현 (Step 4 제거됨)
- `LEVENSHTEIN_IMPLEMENTATION_SUMMARY.md`: Step 3 구현 정리
- `SPECIAL_CASE_ANALYSIS.md`: "…!!" vs "=:" 분석
