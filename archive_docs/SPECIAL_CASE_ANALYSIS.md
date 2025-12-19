# 특수 케이스 분석: "…!!" vs "=:"

## 📊 방법별 성능 비교

| 알고리즘 | 점수 | 설명 |
|---------|------|------|
| **LCS** | 0.000 | 공통 수열 없음 |
| **Levenshtein** | 0.000 | 3글자 모두 다름 (Replace 2 + Delete 1) |
| **Cosine (문자)** | 0.000 | 공통 문자 없음 |
| **Cosine (임베딩)** | ? | 아마 0.0 (의미 완전 다름) |

---

## 🔍 사용자의 통찰 분석

### 관찰
> "=:"를 뒤집으면 "!!"처럼 보인다"

### 정확성
✅ **기하학적으로 맞음**
```
=:  (원본)
 ↓  (180도 회전)
!!  (처럼 보임)
```

### 문제점
❌ **텍스트 레벨에서는 감지 불가능**
- "…!!" = 감탄/강조 (의미: 놀람/흥분)
- "=:" = ? (정체 불명, 아마도 시각적 오류)
- 글자 완전히 다름 → Cosine도 0.0

---

## 🎯 핵심 질문: 이게 해결해야 할 문제일까?

### 시나리오 1: CV 오류
```
원본 이미지: "…!!" (말풍선에 명확히 있음)
CV 감지: "=:" (완전 오인식)
Gemini: "…!!" 또는 "=:" (CV 기반 prompt)

→ 이 경우: Gemini OCR이 올바르게 "…!!"를 인식했으므로 문제 없음
```

### 시나리오 2: 원본 이미지 자체가 "=:"
```
원본 이미지: "=:" (실제로 그렇게 그려짐)
CV 감지: "=:" (정확)
Gemini: "…!!" (해석?)

→ 이 경우: Gemini가 의도를 잘못 해석한 것
→ 텍스트 매칭이 아니라 의도 해석의 문제
```

---

## 💡 Cosine Similarity로 해결 가능?

### 1️⃣ 문자 기반 Cosine
```python
"…!!" vs "=:"
공통 문자: 없음
Cosine = 0.0 ❌
```

### 2️⃣ Word/Subword 임베딩
```python
# 가정: sentence-transformer 사용
vec1 = model.encode("…!!")
vec2 = model.encode("=:")

# 결과: 아마 0.0 (완전히 다른 의미)
```

### 3️⃣ Image 기반 Cosine ⭐
```python
# 이것만 가능할 수도?
img1 = crop_character("…!!")
img2 = crop_character("=:")
vec1 = image_encoder(img1)
vec2 = image_encoder(img2)

# "=:"를 180도 회전하면?
img2_rotated = rotate(img2, 180)
vec2_rot = image_encoder(img2_rotated)
cosine_sim(vec1, vec2_rot)  # 이게 높을 수도?
```

---

## ⚡ 현실적 평가

### Levenshtein이 못하는 이유
```
"…!!" = 3글자
"=:"  = 2글자
공통점 = 0개

편집 거리 = 3 (Replace 2 + Delete 1)
유사도 = 1 - (3/max(3,2)) = 1 - 1.5 = 불가능

→ Levenshtein 정확함 (이건 정말 다른 것)
```

### Cosine이 안 되는 이유
```
텍스트 레벨: 공통 문자 없음 → 0.0
임베딩 레벨: 의미 완전 다름 → 0.0
이미지 레벨: 기하학적 유사성 있음 → 가능성?
```

---

## 🚀 해결책 검토

### ❌ Option 1: Levenshtein 개선
불가능. 실제로 다른 문자임.

### ❌ Option 2: Cosine with Embedding
텍스트 임베딩도 0.0일 가능성 높음 (의미가 다르니까)

### ⚠️ Option 3: Image-based Cosine
```python
def image_char_similarity(char1_img, char2_img) -> float:
    # CNN으로 임베딩
    vec1 = resnet(char1_img)
    vec2 = resnet(char2_img)
    
    # 회전도 시도
    vec2_rot = resnet(rotate(char2_img, 180))
    
    cos_sim_normal = cosine(vec1, vec2)
    cos_sim_rot = cosine(vec1, vec2_rot)
    
    return max(cos_sim_normal, cos_sim_rot)
```

### ✅ Option 4: CV 단계 개선 (현실적)
```
더 좋은 이미지 전처리
→ CV가 "=:"을 "…!!"로 정확히 인식
→ 텍스트 매칭 불필요
```

---

## 🎯 최종 권장사항

### 현재 상황 평가
```
"…!!" vs "=:" 매칭 실패

원인 분석:
1. 텍스트 레벨에서 완전히 다름
2. Levenshtein, Cosine 모두 0.0 (정확)
3. 이건 오류 감지의 문제가 아니라 의도 문제

"=:"가 정말 "…!!"의 오류라면?
→ CV 단계에서 해결해야 함 (텍스트 매칭 X)
→ 이미지 전처리 개선
→ 또는 CV 모델 개선
```

### 추천 순서
1️⃣ **우선**: 현재 75% 성공률로 충분한지 확인
2️⃣ **그 다음**: 25장 전체 테스트해서 "…!!" vs "=:" 같은 케이스 빈도 확인
3️⃣ **필요시**: Image-based 임베딩 (CNN) 도입 (큰 프로젝트)

---

## 📝 결론

| 질문 | 답변 |
|------|------|
| Levenshtein으로 커버 가능? | ❌ (편집 거리 3, 정확히 다름) |
| Cosine으로 커버 가능? | ❌ (공통 문자 없음, 의미 다름) |
| Image Cosine으로 가능? | ⚠️ 가능성 있음 (하지만 복잡도 높음) |
| 현실적 해결책? | CV/전처리 개선 또는 수용 |

**가장 실용적인 접근**: 
- 현재 시스템으로 25장 배치 테스트
- "…!!" 같은 특수 문자 매칭 빈도 측정
- 필요시만 이미지 임베딩 도입

