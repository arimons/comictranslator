"""앞쪽 페이지의 기존 CV로 작품 후보를 찾고, 재검색 결과를 연결과 별도로 보관한다."""
from __future__ import annotations

import re
from pathlib import Path

from pipeline.manual_guidance import load_json, write_json
from pipeline.work_glossary import normalized


def cv_hints(session: dict, work_dir: Path) -> dict:
    labels, pages = [], []
    for item in session.get("images", [])[:3]:
        stem = Path(item.get("input_path") or item.get("name") or item["id"]).stem
        data = load_json(work_dir / "json" / f"{stem}_cv.json", default=[])
        if not isinstance(data, list):
            continue
        values = [str(row.get("label", "")).strip() for row in data if isinstance(row, dict)]
        # 도메인 워터마크와 숫자로만 된 권수는 작품 식별 근거에서 제외한다.
        values = [value for value in values if value and not re.search(r"https?://|\w\.(net|com|org)\b", value, re.I) and not value.isdigit()]
        if values:
            labels.extend(values)
            pages.append(stem)
            break  # 본문을 대량 전송하지 않고 첫 CV 페이지를 사용한다.
    labels = list(dict.fromkeys(labels))[:80]
    # 장식된 일본어 제목이 깨져도 영문 제목은 남을 수 있다.
    roman = [label for label in labels if re.fullmatch(r"[A-Za-z][A-Za-z '-]*", label)]
    japanese = [label for label in labels if re.search(r"[ぁ-んァ-ヶ一-龯]", label)]
    query = " ".join((roman + japanese)[:35])[:300] or session.get("project_name", "")[:300]
    return {"labels": labels, "pages": pages, "query": query, "has_cv": bool(pages)}


def existing_candidates(hints: dict, works: list[dict]) -> list[dict]:
    candidates = []
    labels = [normalized(label) for label in hints["labels"] if len(normalized(label)) >= 2]
    for saved in works:
        work = saved["work"]
        title = normalized(work["title"])
        mask = set()
        matched = []
        for label in labels:
            start = title.find(label)
            if start >= 0:
                mask.update(range(start, start + len(label)))
                matched.append(label)
        author = re.sub(r"原作|漫画|作画|原著|작가|원작|만화", "", work.get("author", ""))
        author_hits = [label for label in labels if label in normalized(author)]
        coverage = len(mask) / max(1, len(title))
        strong = coverage >= 0.4 and len(matched) >= 3 and sum(len(hit) for hit in set(author_hits)) >= 4
        if coverage >= 0.25 and len(matched) >= 2:
            candidates.append({**saved, "id": saved["work_id"], "origin": "existing", "strong": strong,
                               "reason": "확인된 작품의 제목 조각과 작가 단서가 일치" if strong else "기존 작품의 제목 조각 일부가 일치",
                               "matched_keywords": matched + author_hits, "sources": []})
    return sorted(candidates, key=lambda candidate: not candidate["strong"])


class WorkMatcher:
    def read(self, path: Path, hints: dict, works: list[dict]) -> dict:
        saved = load_json(path, default={})
        query = saved.get("query") or hints["query"]
        candidates = existing_candidates(hints, works)
        if saved.get("query"):
            known = {c["work_id"] for c in candidates}
            candidates += [{**w, "id": w["work_id"], "strong": False, "origin": "existing",
                            "reason": "저장된 작품명·작가와 검색어가 일치", "sources": []}
                           for w in works if w["work_id"] not in known and normalized(query) in normalized(" ".join(w["work"].values()))]
        return {"status": "candidates" if candidates else "source_required" if hints["has_cv"] else "waiting_cv",
                "query": query, "hints": hints, "candidates": candidates, "error": ""}

    def start(self, path: Path, query: str, hints: dict, works: list[dict]) -> dict:
        write_json(path, {"query": query})
        return self.read(path, hints, works)

    def confirmed(self, path: Path):
        # 과거의 검색 오류·진행 상태를 유료 호출 없이 정리한다.
        saved = load_json(path, default={})
        write_json(path, {"query": saved.get("query", ""), "status": "confirmed"})
