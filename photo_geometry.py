"""사진의 세부 특징이 촬영 각도가 달라도 같은 위치 관계를 이루는지 확인합니다.

Ps23102004/same의 SIFT + ratio test + RANSAC 구조를 참고해
이 앱의 정규화 좌표, 상호 매칭, 고정 크기 특징값 형식으로 구현했습니다.
출처와 라이선스는 THIRD_PARTY_NOTICES.md에 기록합니다.
"""

import threading

import numpy as np
from PIL import Image

MAX_POINTS = 160
FEATURE_WIDTH = 130  # x, y 정규화 좌표와 SIFT 특징 128개
FEATURE_BYTES = MAX_POINTS * FEATURE_WIDTH * 4
_LOCK = threading.Lock()


def extract(image):
    """원본 대신 물건 중심 영역의 좌표와 특징값만 보관합니다."""
    try:
        import cv2
    except ImportError:
        return None
    preview = image.copy()
    preview.thumbnail((768, 768), Image.Resampling.LANCZOS)
    gray = np.asarray(preview.convert("L"))
    height, width = gray.shape
    mask = np.zeros_like(gray)
    mask[height // 10 : height * 9 // 10, width // 10 : width * 9 // 10] = 255
    points, descriptors = cv2.SIFT_create(nfeatures=MAX_POINTS).detectAndCompute(
        gray, mask
    )
    features = np.zeros((MAX_POINTS, FEATURE_WIDTH), dtype="<f4")
    if descriptors is None:
        return features
    order = sorted(
        range(len(points)), key=lambda index: points[index].response, reverse=True
    )[:MAX_POINTS]
    for target, source in enumerate(order):
        features[target, :2] = np.asarray(points[source].pt) / (width, height)
        descriptor = descriptors[source]
        features[target, 2:] = descriptor / max(float(np.linalg.norm(descriptor)), 1e-9)
    return features


def compare(reference, candidate):
    """서로 고유하게 대응하는 점 중 동일한 변환을 따르는 점 수를 반환합니다."""
    if reference is None or candidate is None:
        return 0, 0, 0.0
    try:
        import cv2
    except ImportError:
        return 0, 0, 0.0

    first = reference[np.linalg.norm(reference[:, 2:], axis=1) > 0]
    second = candidate[np.linalg.norm(candidate[:, 2:], axis=1) > 0]
    if min(len(first), len(second)) < 4:
        return 0, 0, 0.0
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    forward = matcher.knnMatch(first[:, 2:].copy(), second[:, 2:].copy(), k=2)
    backward = matcher.knnMatch(second[:, 2:].copy(), first[:, 2:].copy(), k=2)
    reverse = {
        pair[0].queryIdx: pair[0].trainIdx
        for pair in backward
        if len(pair) == 2 and pair[0].distance < 0.8 * pair[1].distance
    }
    matches = [
        pair[0]
        for pair in forward
        if len(pair) == 2
        and pair[0].distance < 0.8 * pair[1].distance
        and reverse.get(pair[0].trainIdx) == pair[0].queryIdx
    ]
    if len(matches) < 6:
        return 0, len(matches), 0.0
    source = np.float32([first[match.queryIdx, :2] for match in matches])
    target = np.float32([second[match.trainIdx, :2] for match in matches])
    # 두 사진의 공통된 배경 한 점에 반복 대응하는 매칭은 상호 검사로 제외합니다.
    with _LOCK:
        cv2.setRNGSeed(0)
        transform, mask = cv2.estimateAffinePartial2D(
            source,
            target,
            method=cv2.RANSAC,
            ransacReprojThreshold=0.015,
            maxIters=2000,
        )
    inliers = int(mask.sum()) if transform is not None and mask is not None else 0
    fraction = inliers / len(matches)
    # 무늬가 적거나 작은 배경 조각만 맞는 경우에는 점수를 보강하지 않습니다.
    coherent = (
        mask.ravel().astype(bool)
        if mask is not None
        else np.zeros(len(matches), dtype=bool)
    )
    spread = np.ptp(source[coherent], axis=0) if inliers else np.zeros(2)
    target_spread = np.ptp(target[coherent], axis=0) if inliers else np.zeros(2)
    supported = (
        inliers >= 15 and fraction >= 0.65 and min(*spread, *target_spread) >= 0.15
    )
    strength = min(inliers / 30, 1) * fraction if supported else 0.0
    return inliers, len(matches), strength
