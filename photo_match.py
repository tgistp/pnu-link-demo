"""Optional DINOv2 photo ranking for existing lost-report candidates."""

import hashlib
import struct
from functools import lru_cache
from pathlib import Path
from threading import Lock

import numpy as np
from PIL import Image, ImageOps

from security import SecurityError, image_path
from uploads import read_image
import photo_geometry
import photo_descriptor

MODEL_DIR = Path(__file__).resolve().parent / "models/photo-dinov2"
MODEL_PATH = MODEL_DIR / "vision-int8.onnx"
MODEL_HASH = "3afdc8bc63b50558d6e5770f5b799bb82455c2311183a2de43803f343a29d917"
EMBEDDING_TAG = b"dinov2-small-int8-cls-v1:"
BUNDLE_TAG = b"pnu-photo-set-v1:"
COLOR_CHANNELS = 3
COLOR_WEIGHT = 0.7
DIMENSIONS = 384
PATCH_GRID = 16
DETAIL_BORDER = 3
DETAIL_COUNT = (PATCH_GRID - 2 * DETAIL_BORDER) ** 2
DETAIL_WEIGHT = 0.75
GLOBAL_BYTES = DIMENSIONS * 4
COLOR_BYTES = COLOR_CHANNELS * 4
DETAIL_BYTES = DETAIL_COUNT * DIMENSIONS * 2
PHOTO_WEIGHT = 20
MAX_CANDIDATES = 12
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_model = None
_model_lock = Lock()


def _get_model():
    """체크섬을 검증한 DINOv2 모델을 처음 사용할 때만 메모리에 올립니다."""
    global _model
    with _model_lock:
        if _model is None:
            try:
                import onnxruntime as ort

                with MODEL_PATH.open("rb") as file:
                    if hashlib.file_digest(file, "sha256").hexdigest() != MODEL_HASH:
                        raise ValueError("Unexpected model hash")
                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                options.inter_op_num_threads = 1
                _model = ort.InferenceSession(
                    str(MODEL_PATH),
                    sess_options=options,
                    providers=["CPUExecutionProvider"],
                )
            except Exception as exc:
                raise SecurityError(
                    "사진 비교 모델을 열 수 없습니다. 모델 파일을 확인하거나 사진 없이 신고해 주세요."
                ) from exc
    return _model


def _hidden_features(image):
    """사진을 모델 입력 크기로 맞춰 전체 및 위치별 외형 특징을 추출합니다."""
    width, height = image.size
    edge = min(width, height)
    left, top = (width - edge) // 2, (height - edge) // 2
    square = image.crop((left, top, left + edge, top + edge))
    resized = square.resize((256, 256), Image.Resampling.BICUBIC)
    cropped = resized.crop((16, 16, 240, 240))
    pixels = (np.asarray(cropped, dtype=np.float32) / 255 - MEAN) / STD
    tensor = np.ascontiguousarray(pixels.transpose(2, 0, 1)[None])
    try:
        (hidden,) = _get_model().run(["last_hidden_state"], {"pixel_values": tensor})
        return hidden[0].astype("<f4")
    except SecurityError:
        raise
    except Exception as exc:
        raise SecurityError(
            "사진 특징을 비교할 수 없습니다. 다른 사진을 사용해 주세요."
        ) from exc


def _image_vectors(image):
    hidden = _hidden_features(image)
    if hidden.shape != (1 + PATCH_GRID**2, DIMENSIONS):
        raise SecurityError(
            "사진 특징을 비교할 수 없습니다. 모델 파일을 확인해 주세요."
        )
    norms = np.linalg.norm(hidden, axis=1, keepdims=True)
    if not np.isfinite(norms).all() or (norms == 0).any():
        raise SecurityError(
            "사진 특징을 비교할 수 없습니다. 다른 사진을 사용해 주세요."
        )
    normalized = hidden / norms
    # 중심부의 작은 영역을 따로 비교해 배경과 촬영 각도의 영향을 줄입니다.
    grid = normalized[1:].reshape(PATCH_GRID, PATCH_GRID, DIMENSIONS)
    center = grid[DETAIL_BORDER:-DETAIL_BORDER, DETAIL_BORDER:-DETAIL_BORDER]
    return normalized[0], center.reshape(DETAIL_COUNT, DIMENSIONS).astype("<f2")


def embed_upload(upload):
    with read_image(upload) as image:
        # 원본 사진 대신 전체 외형, 색상, 중심부의 부분 특징을 저장합니다.
        vector, details = _image_vectors(image)
        saved = (
            EMBEDDING_TAG
            + vector.tobytes()
            + _photo_color(image).tobytes()
            + details.tobytes()
        )
        geometry = photo_geometry.extract(image)
        if geometry is None:
            geometry = np.zeros(
                (photo_geometry.MAX_POINTS, photo_geometry.FEATURE_WIDTH), dtype="<f4"
            )
        # 기존 특징을 앞에 보존하고 새 전체 영역 자료를 뒤에 추가해 이전 신고도 읽습니다.
        return (
            saved + geometry.tobytes() + photo_descriptor.extract(image, _get_model())
        )


def embed_uploads(uploads):
    """최대 3장의 특징을 저장하며 기존 한 장 형식도 그대로 읽습니다."""
    if not 1 <= len(uploads) <= 3:
        raise SecurityError("사진은 최대 3장까지 비교할 수 있습니다.")
    features = [embed_upload(upload) for upload in uploads]
    return _pack_photos(features)


def _pack_photos(features):
    """한 장과 여러 장의 저장 형식을 한곳에서 구성합니다."""
    if len(features) == 1:
        return features[0]
    return (
        BUNDLE_TAG
        + bytes([len(features)])
        + b"".join(struct.pack("<I", len(feature)) + feature for feature in features)
    )


def unpack_photos(value):
    if not isinstance(value, bytes):
        raise SecurityError("저장된 사진 특징을 읽을 수 없습니다.")
    if not value.startswith(BUNDLE_TAG):
        unpack_embedding(value)
        return [value]
    offset = len(BUNDLE_TAG)
    if len(value) <= offset or not 2 <= value[offset] <= 3:
        raise SecurityError("저장된 사진 묶음을 읽을 수 없습니다.")
    count = value[offset]
    offset += 1
    photos = []
    for _ in range(count):
        if len(value) < offset + 4:
            raise SecurityError("저장된 사진 묶음을 읽을 수 없습니다.")
        size = struct.unpack_from("<I", value, offset)[0]
        offset += 4
        photo = value[offset : offset + size]
        if not photo.startswith(EMBEDDING_TAG):
            raise SecurityError("저장된 사진 특징을 읽을 수 없습니다.")
        unpack_embedding(photo)
        photos.append(photo)
        offset += size
    if offset != len(value):
        raise SecurityError("저장된 사진 묶음을 읽을 수 없습니다.")
    return photos


def unpack_embedding(value):
    if isinstance(value, bytes) and value.startswith(BUNDLE_TAG):
        return unpack_embedding(unpack_photos(value)[0])
    if (
        not isinstance(value, bytes)
        or not value.startswith(EMBEDDING_TAG)
        or len(value) - len(EMBEDDING_TAG)
        not in (
            GLOBAL_BYTES,
            GLOBAL_BYTES + COLOR_BYTES,
            GLOBAL_BYTES + COLOR_BYTES + DETAIL_BYTES,
            GLOBAL_BYTES + COLOR_BYTES + DETAIL_BYTES + photo_geometry.FEATURE_BYTES,
            GLOBAL_BYTES
            + COLOR_BYTES
            + DETAIL_BYTES
            + photo_geometry.FEATURE_BYTES
            + photo_descriptor.BYTES,
        )
    ):
        raise SecurityError("저장된 사진 특징을 읽을 수 없습니다.")
    vector = np.frombuffer(
        value, dtype="<f4", offset=len(EMBEDDING_TAG), count=DIMENSIONS
    )
    if not np.isclose(np.linalg.norm(vector), 1.0, atol=1e-3):
        raise SecurityError("저장된 사진 특징을 읽을 수 없습니다.")
    color = _unpack_color(value)
    if color is not None and not ((0 <= color) & (color <= 1)).all():
        raise SecurityError("저장된 사진 색상 특징을 읽을 수 없습니다.")
    details = _unpack_details(value)
    if details is not None and not np.allclose(
        np.linalg.norm(details, axis=1), 1.0, atol=1e-3
    ):
        raise SecurityError("저장된 사진 부분 특징을 읽을 수 없습니다.")
    geometry = _unpack_geometry(value)
    if geometry is not None and (
        not np.isfinite(geometry).all()
        or not ((geometry[:, :2] >= 0) & (geometry[:, :2] <= 1)).all()
        or not ((geometry[:, 2:] >= 0) & (geometry[:, 2:] <= 1)).all()
    ):
        raise SecurityError("저장된 사진 대응 특징을 읽을 수 없습니다.")
    extension = _object_extension(value)
    if extension is not None:
        photo_descriptor.unpack(extension)
    return vector


def _unpack_color(value):
    # Older reports contain only the DINOv2 vector.
    offset = len(EMBEDDING_TAG) + GLOBAL_BYTES
    return (
        np.frombuffer(value, dtype="<f4", offset=offset, count=COLOR_CHANNELS)
        if len(value) > offset
        else None
    )


def _unpack_details(value):
    offset = len(EMBEDDING_TAG) + GLOBAL_BYTES + COLOR_BYTES
    if len(value) > offset:
        return (
            np.frombuffer(
                value, dtype="<f2", offset=offset, count=DETAIL_COUNT * DIMENSIONS
            )
            .astype(np.float32)
            .reshape(DETAIL_COUNT, DIMENSIONS)
        )
    return None


def _unpack_geometry(value):
    offset = len(EMBEDDING_TAG) + GLOBAL_BYTES + COLOR_BYTES + DETAIL_BYTES
    if len(value) > offset:
        return np.frombuffer(
            value,
            dtype="<f4",
            offset=offset,
            count=photo_geometry.MAX_POINTS * photo_geometry.FEATURE_WIDTH,
        ).reshape(photo_geometry.MAX_POINTS, photo_geometry.FEATURE_WIDTH)
    return None


def _object_extension(value):
    offset = (
        len(EMBEDDING_TAG)
        + GLOBAL_BYTES
        + COLOR_BYTES
        + DETAIL_BYTES
        + photo_geometry.FEATURE_BYTES
    )
    return value[offset:] if len(value) == offset + photo_descriptor.BYTES else None


def _photo_color(image):
    pixels = (
        np.asarray(image.resize((32, 32), Image.Resampling.BOX), dtype=np.float32) / 255
    )
    border = np.concatenate((pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]))
    center = pixels[5:-5, 5:-5].reshape(-1, COLOR_CHANNELS)
    # Reduce the influence of colors that also occur near the photo edges.
    distances = np.linalg.norm(center[:, None] - border, axis=-1).min(axis=1)
    return np.median(center[distances >= np.median(distances)], axis=0).astype("<f4")


def _color_similarity(first, second):
    difference = first - second
    # Exposure can change brightness without changing the object's color.
    brightness = float(difference.mean())
    distance = np.linalg.norm(difference - brightness) + 0.4 * abs(brightness)
    return float(np.clip(1 - distance / 0.25, 0, 1))


def _combine_similarity(visual_similarity, color_similarity):
    # Squaring color agreement separates close color matches from partial matches.
    return (1 - COLOR_WEIGHT) * visual_similarity + COLOR_WEIGHT * color_similarity**2


def _detail_similarity(reference, candidate):
    # 좌표를 고정하지 않고 양쪽 부분 특징의 가장 가까운 상대를 찾습니다.
    matches = reference @ candidate.astype(np.float32).T
    return float(
        np.clip((matches.max(axis=0).mean() + matches.max(axis=1).mean()) / 2, -1, 1)
    )


@lru_cache(maxsize=256)
def _found_embedding(path, modified_ns, file_size):
    with Image.open(path) as image:
        rgb = ImageOps.exif_transpose(image).convert("RGB")
        vector, details = _image_vectors(rgb)
        return vector, _photo_color(rgb), details


@lru_cache(maxsize=256)
def _found_geometry(path, modified_ns, file_size):
    with Image.open(path) as image:
        return photo_geometry.extract(ImageOps.exif_transpose(image).convert("RGB"))


def rerank(results, saved_embedding, data_dir, *, report_color=None, image_lookup=None):
    """기존 상위 후보의 사진 점수를 계산해 총점을 더하고 순위를 다시 정합니다."""
    photos = unpack_photos(saved_embedding)
    if all(_object_extension(photo) is not None for photo in photos):
        detailed = _rerank_objects(
            results, photos, data_dir, report_color, image_lookup
        )
        compared = detailed.loc[detailed.photo_similarity.notna()].sort_values(
            "photo_score", ascending=False
        )
        if (
            len(compared) > 1
            and float(compared.iloc[0].photo_score - compared.iloc[1].photo_score) < 1
        ):
            # 세부 대응이 애매하면 그 결과로 기존 순위를 뒤집지 않습니다.
            # 소유 확정이나 높은 정확도로 표현하지 않고 기존 비교로 되돌린 사실을 표시합니다.
            legacy = rerank(
                results,
                _pack_photos([photo[: -photo_descriptor.BYTES] for photo in photos]),
                data_dir,
                report_color=report_color,
                image_lookup=image_lookup,
            )
            legacy.attrs.update(photo_engine="object-v2-fallback", photo_fallback=True)
            return legacy
        return detailed
    references = [
        (
            unpack_embedding(photo),
            _unpack_color(photo),
            _unpack_details(photo),
            _unpack_geometry(photo),
        )
        for photo in unpack_photos(saved_embedding)
    ]
    ranked = results.copy()
    ranked["base_rank"] = np.arange(1, len(ranked) + 1)
    ranked["photo_similarity"] = np.nan
    ranked["geometry_inliers"] = np.nan
    ranked["geometry_matches"] = np.nan
    ranked["matched_photo"] = np.nan
    ranked["photo_visual"] = np.nan
    ranked["photo_details"] = np.nan
    used_color = False
    for row in ranked.head(MAX_CANDIDATES).itertuples():
        relative = (
            image_lookup.get(row.id, row.image)
            if image_lookup is not None
            else row.image
        )
        paths = [relative] if isinstance(relative, str) else relative
        if not isinstance(paths, list) or len(paths) > 3:
            raise SecurityError("비교할 사진 목록을 확인해 주세요.")
        best = None
        for index, relative in enumerate(paths, 1):
            path = image_path(data_dir, relative)
            if path is None:
                continue
            try:
                stat = path.stat()
                candidate, candidate_color, candidate_details = _found_embedding(
                    path, stat.st_mtime_ns, stat.st_size
                )
                geometry = (
                    _found_geometry(path, stat.st_mtime_ns, stat.st_size)
                    if any(ref[3] is not None for ref in references)
                    else None
                )
            except OSError:
                continue
            for (
                reference,
                reference_color,
                reference_details,
                reference_geometry,
            ) in references:
                visual = float(np.clip(reference @ candidate, -1, 1))
                score, detail = visual, np.nan
                registered_color = report_color not in (None, "", "전체", "기타")
                # 등록 색상이 있으면 색상 항목에서만 채점하고 사진은 외형·세부 특징을 비교합니다.
                if registered_color and reference_details is not None:
                    detail = _detail_similarity(reference_details, candidate_details)
                    score = (1 - DETAIL_WEIGHT) * score + DETAIL_WEIGHT * detail
                elif reference_color is not None and not registered_color:
                    score = _combine_similarity(
                        score, _color_similarity(reference_color, candidate_color)
                    )
                    used_color = True
                inliers, matches, strength = photo_geometry.compare(
                    reference_geometry, geometry
                )
                score += 0.15 * strength * (1 - score)
                # 사진 수에 가산점을 주지 않고 가장 잘 맞는 조합의 점수만 사용합니다.
                if best is None or score > best[0]:
                    best = (score, inliers, matches, index, visual, detail)
        if best is not None:
            ranked.loc[
                row.Index,
                [
                    "photo_similarity",
                    "geometry_inliers",
                    "geometry_matches",
                    "matched_photo",
                    "photo_visual",
                    "photo_details",
                ],
            ] = best
    ranked["photo_score"] = PHOTO_WEIGHT * ranked.photo_similarity.fillna(0).clip(
        lower=0
    )
    ranked["combined_score"] = ranked.score + ranked.photo_score
    if ranked.photo_similarity.notna().any():
        ranked.attrs["photo_color_used"] = used_color
        ranked.attrs["photo_details_available"] = any(
            ref[2] is not None for ref in references
        )
        ranked.attrs["photo_geometry_available"] = any(
            ref[3] is not None for ref in references
        )
        ranked.attrs["score_limits"] = {
            **ranked.attrs.get("score_limits", {}),
            "photo_score": PHOTO_WEIGHT,
        }
        ranked.attrs["score_max"] = ranked.attrs.get("score_max", 100) + PHOTO_WEIGHT
    return ranked.sort_values("combined_score", ascending=False, kind="stable")


@lru_cache(maxsize=128)
def _found_object(path, modified_ns, file_size):
    with Image.open(path) as image:
        rgb = ImageOps.exif_transpose(image).convert("RGB")
        return photo_descriptor.extract(rgb, _get_model()), _photo_color(rgb)


def _rerank_objects(results, photos, data_dir, report_color, image_lookup):
    """새 신고는 전체 물건의 고유 대응을 비교하고 부족한 대응은 점수에 그대로 반영합니다."""
    ranked = results.copy()
    ranked["base_rank"] = np.arange(1, len(ranked) + 1)
    columns = [
        "photo_similarity",
        "geometry_inliers",
        "geometry_matches",
        "matched_photo",
        "photo_visual",
        "photo_details",
        "photo_coverage",
        "photo_spatial_agreement",
    ]
    for name in columns:
        ranked[name] = np.nan
    use_color = report_color in (None, "", "전체", "기타")
    references = [(_object_extension(photo), _unpack_color(photo)) for photo in photos]
    for row in ranked.head(MAX_CANDIDATES).itertuples():
        paths = (
            image_lookup.get(row.id, row.image)
            if image_lookup is not None
            else row.image
        )
        paths = [paths] if isinstance(paths, str) else paths
        if not isinstance(paths, list) or len(paths) > 3:
            raise SecurityError("비교할 사진 목록을 확인해 주세요.")
        best = None
        for number, relative in enumerate(paths, 1):
            path = image_path(data_dir, relative)
            if path is None:
                continue
            try:
                stat = path.stat()
                candidate, color = _found_object(
                    str(path), stat.st_mtime_ns, stat.st_size
                )
            except (OSError, SecurityError):
                # 손상된 후보 사진은 일치로 처리하지 않고 해당 사진만 비교에서 제외합니다.
                continue
            for reference, ref_color in references:
                score, detail = photo_descriptor.compare(reference, candidate)
                if use_color and ref_color is not None:
                    score = _combine_similarity(
                        score, _color_similarity(ref_color, color)
                    )
                values = [
                    score,
                    detail["unique_matches"] * detail["spatial_agreement"],
                    detail["unique_matches"],
                    number,
                    detail["global_similarity"],
                    detail["detail_similarity"],
                    detail["coverage"],
                    detail["spatial_agreement"],
                ]
                if best is None or score > best[0]:
                    best = values
        if best is not None:
            ranked.loc[row.Index, columns] = best
    ranked["photo_score"] = PHOTO_WEIGHT * ranked.photo_similarity.fillna(0).clip(
        lower=0
    )
    ranked["combined_score"] = ranked.score + ranked.photo_score
    ranked.attrs.update(
        photo_color_used=use_color,
        photo_details_available=True,
        photo_geometry_available=True,
        photo_engine="object-v2",
        score_limits={
            **results.attrs.get("score_limits", {}),
            "photo_score": PHOTO_WEIGHT,
        },
        score_max=results.attrs.get("score_max", 100) + PHOTO_WEIGHT,
    )
    return ranked.sort_values("combined_score", ascending=False, kind="stable")
