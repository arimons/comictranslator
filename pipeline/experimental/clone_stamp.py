# 클론 스탬프: 마스크 영역을 같은 이미지의 다른 위치 픽셀로 채운다
"""배경 복원을 '생성'이 아니라 '복사'로 처리한다.

인페인팅과 달리 없는 픽셀을 만들어내지 않으므로 뭉개짐이 원리적으로 없다.
대신 복사해올 소스가 이미지 안에 존재해야 한다.
"""
from __future__ import annotations

import cv2
import numpy as np


def find_clone_offset(
    image: np.ndarray,
    mask: np.ndarray,
    max_shift: int = 160,
    ring_px: int = 14,
    min_coverage: float = 0.95,
) -> tuple[tuple[int, int], float, float]:
    """마스크를 벗어나면서 주변 링이 가장 잘 맞는 이동량을 찾는다.

    두 조건을 함께 만족해야 한다.
    1. coverage: 마스크 화소의 소스가 이미지 안이고 마스크 밖일 것.
       이 제약이 없으면 글자 폭보다 작은 오프셋이 뽑혀 글자를 글자로 덮게 된다.
    2. SSD: 링 영역이 이동 후에도 잘 맞을 것 (배경 주기와 정합).

    반환: ((dx, dy), SSD, coverage)
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    outer = cv2.dilate(mask, np.ones((ring_px * 2 + 1,) * 2, np.uint8), 1)
    ring = (outer > 0) & (mask == 0)
    if int(np.count_nonzero(ring)) < 40:
        return (0, 0), float("inf"), 0.0

    ys, xs = np.nonzero(ring)
    my, mx = np.nonzero(mask)
    h, w = gray.shape
    best, best_score, best_cov = (0, 0), float("inf"), 0.0

    for dy in range(-max_shift, max_shift + 1, 2):
        for dx in range(-max_shift, max_shift + 1, 2):
            if abs(dx) < 3 and abs(dy) < 3:
                continue
            # 조건 1: 마스크 화소가 실제로 채워지는 비율
            sy, sx = my + dy, mx + dx
            inb = (sy >= 0) & (sy < h) & (sx >= 0) & (sx < w)
            if not inb.any():
                continue
            cov = float(
                np.count_nonzero(inb & (mask[np.clip(sy, 0, h - 1), np.clip(sx, 0, w - 1)] == 0))
            ) / len(my)
            if cov < min_coverage:
                continue
            # 조건 2: 링 정합도
            ry, rx = ys + dy, xs + dx
            ok = (ry >= 0) & (ry < h) & (rx >= 0) & (rx < w)
            if ok.sum() < len(ys) * 0.6:
                continue
            clean = mask[ry[ok], rx[ok]] == 0
            if clean.sum() < len(ys) * 0.4:
                continue
            diff = gray[ys[ok][clean], xs[ok][clean]] - gray[ry[ok][clean], rx[ok][clean]]
            score = float(np.mean(diff * diff))
            if score < best_score:
                best_score, best, best_cov = score, (dx, dy), cov

    return best, best_score, best_cov


def apply_clone_stamp(
    image: np.ndarray,
    mask: np.ndarray,
    offset: tuple[int, int],
    feather_px: int = 2,
    passes: int = 3,
) -> np.ndarray:
    """마스크 영역을 offset만큼 떨어진 위치의 픽셀로 채운다.

    소스가 마스크와 겹치면 그 화소는 다음 pass로 미룬다.
    passes를 반복해 남은 구멍을 줄인다.
    """
    out = image.copy()
    remaining = mask.copy()
    dx, dy = offset
    h, w = mask.shape

    for step in range(1, passes + 1):
        ys, xs = np.nonzero(remaining)
        if len(ys) == 0:
            break
        sy, sx = ys + dy * step, xs + dx * step
        ok = (sy >= 0) & (sy < h) & (sx >= 0) & (sx < w)
        ok &= mask[np.clip(sy, 0, h - 1), np.clip(sx, 0, w - 1)] == 0
        if not ok.any():
            break
        out[ys[ok], xs[ok]] = image[sy[ok], sx[ok]]
        remaining[ys[ok], xs[ok]] = 0

    if feather_px > 0:
        # 경계만 부드럽게. 내부는 복사본을 그대로 쓴다.
        alpha = cv2.GaussianBlur(mask.astype(np.float32) / 255.0,
                                 (feather_px * 2 + 1,) * 2, 0)[..., None]
        out = (out * alpha + image * (1 - alpha)).astype(np.uint8)
        out[mask > 0] = np.where(
            (remaining > 0)[..., None][mask > 0], image[mask > 0], out[mask > 0]
        )
    return out


def restoration_score(image: np.ndarray, restored: np.ndarray, mask: np.ndarray) -> dict:
    """복원 품질을 시각 판단 없이 잰다.

    지운 영역과 주변 링의 통계가 이어지면 잘 복원된 것으로 본다.
    """
    g0 = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    g1 = cv2.cvtColor(restored, cv2.COLOR_BGR2GRAY)
    ring = (cv2.dilate(mask, np.ones((25, 25), np.uint8), 1) > 0) & (mask == 0)
    inside = mask > 0
    if not ring.any() or not inside.any():
        return {}

    def stats(img, sel):
        v = img[sel].astype(np.float32)
        e = cv2.Canny(img, 80, 160)[sel]
        return float(v.mean()), float(v.std()), float(np.count_nonzero(e)) / sel.sum()

    rm, rs, re = stats(g1, ring)
    im, isd, ie = stats(g1, inside)
    # 지운 자리에 글자 신호가 남았는가
    th = cv2.morphologyEx(g1, cv2.MORPH_TOPHAT, np.ones((13, 13), np.uint8))
    residual = float(np.count_nonzero((th > 60) & inside)) / inside.sum()
    return {
        "mean_gap": round(abs(im - rm), 2),
        "std_gap": round(abs(isd - rs), 2),
        "edge_gap": round(abs(ie - re), 4),
        "residual_text": round(residual, 4),
    }
