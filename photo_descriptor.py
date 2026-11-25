"""물건 전체의 세부 특징을 보관하고 고유 대응과 위치 일관성을 함께 비교합니다.

기존 중심부 특징과 달리 전체 물건을 정사각 입력 안에 맞춥니다.
점수는 소유 확률이 아니며, 같은 제품의 개별 소유자를 구분하지 않습니다.
"""

import threading
import numpy as np
from PIL import Image
from security import SecurityError

TAG = b"pnu-object-v2:"
GRID = 16
DIM = 384
PAYLOAD_BYTES = DIM * 4 + GRID * GRID * DIM * 2 + GRID * GRID * 2
BYTES = len(TAG) + PAYLOAD_BYTES
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_LOCK = threading.Lock()


def object_canvas(image):
    """배경 후보를 분리하되 물건 전체를 보존합니다. 분리 실패 시 원본 전체를 사용합니다."""
    import cv2

    preview = image.convert("RGB")
    preview.thumbnail((320, 320), Image.Resampling.LANCZOS)
    rgb = np.asarray(preview).copy()
    height, width = rgb.shape[:2]
    mask = np.ones((height, width), np.uint8)
    if min(width, height) >= 32:
        try:
            labels = np.zeros(mask.shape, np.uint8)
            with _LOCK:
                cv2.setRNGSeed(0)
                cv2.grabCut(
                    rgb,
                    labels,
                    (2, 2, width - 4, height - 4),
                    np.zeros((1, 65)),
                    np.zeros((1, 65)),
                    3,
                    cv2.GC_INIT_WITH_RECT,
                )
            foreground = np.isin(labels, [cv2.GC_FGD, cv2.GC_PR_FGD]).astype("uint8")
            count, components, stats, _ = cv2.connectedComponentsWithStats(
                foreground, 8
            )
            if count > 1:
                largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
                candidate = (components == largest).astype("uint8")
                if 0.025 <= float(candidate.mean()) <= 0.9:
                    mask = candidate
        except cv2.error:
            pass
    yy, xx = np.where(mask)
    x0, x1 = max(0, int(xx.min()) - 6), min(width, int(xx.max()) + 7)
    y0, y1 = max(0, int(yy.min()) - 6), min(height, int(yy.max()) + 7)
    rgb[mask == 0] = (MEAN * 255).astype("uint8")
    object_image = Image.fromarray(rgb[y0:y1, x0:x1])
    object_mask = Image.fromarray(mask[y0:y1, x0:x1] * 255)
    ratio = min(224 / object_image.width, 224 / object_image.height)
    size = (
        max(1, round(object_image.width * ratio)),
        max(1, round(object_image.height * ratio)),
    )
    origin = ((224 - size[0]) // 2, (224 - size[1]) // 2)
    canvas = Image.new("RGB", (224, 224), tuple((MEAN * 255).astype(int)))
    canvas.paste(object_image.resize(size, Image.Resampling.BICUBIC), origin)
    alpha = Image.new("L", (224, 224))
    alpha.paste(object_mask.resize(size, Image.Resampling.BILINEAR), origin)
    return np.asarray(canvas, dtype=np.float32) / 255, np.asarray(
        alpha, dtype=np.float32
    ) / 255


def extract(image, model):
    """전체 외형, 모든 16x16 부분 특징, 물건 영역의 중요도를 추가 자료로 저장합니다."""
    pixels, alpha = object_canvas(image)
    tensor = np.ascontiguousarray(((pixels - MEAN) / STD).transpose(2, 0, 1)[None])
    (hidden,) = model.run(["last_hidden_state"], {"pixel_values": tensor})
    hidden = hidden[0].astype("float32")
    if hidden.shape != (257, DIM) or not np.isfinite(hidden).all():
        raise SecurityError("사진 특징을 계산할 수 없습니다.")
    hidden /= np.maximum(np.linalg.norm(hidden, axis=1, keepdims=True), 1e-8)
    # 고대비 로고·무늬 주변을 보강하되 무늬 없는 물건 영역도 비교 대상에 남깁니다.
    gray = pixels.mean(axis=2)
    gy, gx = np.gradient(gray)
    energy = np.hypot(gx, gy).reshape(GRID, 14, GRID, 14).mean(axis=(1, 3))
    foreground = alpha.reshape(GRID, 14, GRID, 14).mean(axis=(1, 3))
    scale = max(float(np.percentile(energy, 90)), 1e-5)
    weights = (foreground * (0.35 + 0.65 * np.clip(energy / scale, 0, 1))).flatten()
    return (
        TAG
        + hidden[0].astype("<f4").tobytes()
        + hidden[1:].astype("<f2").tobytes()
        + weights.astype("<f2").tobytes()
    )


def unpack(value):
    if not isinstance(value, bytes) or len(value) != BYTES or not value.startswith(TAG):
        raise SecurityError("사진의 전체 영역 특징을 읽을 수 없습니다.")
    offset = len(TAG)
    vector = np.frombuffer(value, dtype="<f4", count=DIM, offset=offset).copy()
    offset += DIM * 4
    patches = (
        np.frombuffer(value, dtype="<f2", count=256 * DIM, offset=offset)
        .astype("float32")
        .reshape(256, DIM)
    )
    offset += 256 * DIM * 2
    weights = np.frombuffer(value, dtype="<f2", count=256, offset=offset).astype(
        "float32"
    )
    if (
        not np.isfinite(vector).all()
        or not np.isfinite(patches).all()
        or not np.isfinite(weights).all()
        or not np.isclose(np.linalg.norm(vector), 1, atol=0.002)
        or not np.allclose(np.linalg.norm(patches, axis=1), 1, atol=0.003)
        or not ((weights >= 0) & (weights <= 1)).all()
        or (weights > 0.05).sum() < 4
    ):
        raise SecurityError("사진 특징의 값이 올바르지 않습니다.")
    return vector, patches, weights


def compare(first, second):
    """범주 공통 성분을 덜어내고 양방향 고유 대응의 범위·위치 일관성을 확인합니다."""
    import cv2

    a, pa, wa = unpack(first)
    b, pb, wb = unpack(second)
    ia = np.flatnonzero(wa > 0.08)
    ib = np.flatnonzero(wb > 0.08)
    x = pa[ia] - np.average(pa[ia], axis=0, weights=wa[ia])
    y = pb[ib] - np.average(pb[ib], axis=0, weights=wb[ib])
    x /= np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-8)
    y /= np.maximum(np.linalg.norm(y, axis=1, keepdims=True), 1e-8)
    similarities = x @ y.T
    right = similarities.argmax(axis=1)
    left = similarities.argmax(axis=0)
    chosen = np.flatnonzero(
        (left[right] == np.arange(len(ia)))
        & (similarities[np.arange(len(ia)), right] > 0.3)
    )
    partner = right[chosen]
    mass_a = wa[ia]
    mass_b = wb[ib]
    coverage = 0.5 * (
        mass_a[chosen].sum() / mass_a.sum() + mass_b[partner].sum() / mass_b.sum()
    )
    agreement = 0.0
    if len(chosen) >= 6:
        positions = (
            np.stack(np.meshgrid(np.arange(GRID), np.arange(GRID)), axis=-1)
            .reshape(-1, 2)
            .astype("float32")
            / GRID
        )
        with _LOCK:
            cv2.setRNGSeed(0)
            transform, inliers = cv2.estimateAffinePartial2D(
                positions[ia[chosen]],
                positions[ib[partner]],
                method=cv2.RANSAC,
                ransacReprojThreshold=0.08,
            )
        if transform is not None and inliers is not None:
            agreement = float(inliers.mean())
    paired = (
        float(np.average(similarities[chosen, partner], weights=mass_a[chosen]))
        if len(chosen)
        else 0.0
    )
    distinct = paired * np.sqrt(float(coverage))
    nearest = 0.5 * (
        np.average(similarities.max(axis=1), weights=mass_a)
        + np.average(similarities.max(axis=0), weights=mass_b)
    )
    global_score = float(np.clip(a @ b, 0, 1))
    score = (
        0.30 * global_score
        + 0.35 * distinct
        + 0.20 * float(np.clip(nearest, 0, 1))
        + 0.15 * distinct * agreement
    )
    return float(np.clip(score, 0, 1)), dict(
        global_similarity=global_score,
        detail_similarity=distinct,
        unique_matches=len(chosen),
        coverage=float(coverage),
        spatial_agreement=agreement,
    )
