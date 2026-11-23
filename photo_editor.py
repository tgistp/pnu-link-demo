"""업로드한 사진별 영역 선택을 등록과 분실 신고에서 공통으로 사용합니다."""
import hashlib
import io
import math

import streamlit as st
from streamlit_cropper import st_cropper

from security import SecurityError, image_path
from uploads import MAX_UPLOAD_MB, read_image, save_image

MAX_PHOTOS = 3


def prepare_upload(entry):
    """선택 영역만 새 JPEG로 정리해 저장·모델 입력에 같은 사진을 사용합니다."""
    with read_image(entry["upload"]) as image:
        box = entry.get("crop", (0, 0, 1, 1))
        if len(box) != 4 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in box):
            raise SecurityError("사진 선택 영역을 다시 지정해 주세요.")
        left, top, right, bottom = box
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise SecurityError("사진 선택 영역을 다시 지정해 주세요.")
        bounds = (int(left * image.width), int(top * image.height),
                  int(right * image.width), int(bottom * image.height))
        if bounds[2] - bounds[0] < 1 or bounds[3] - bounds[1] < 1:
            raise SecurityError("물건 전체가 포함되도록 영역을 조금 넓혀 주세요.")
        with image.crop(bounds) as cropped:
            result = io.BytesIO()
            cropped.save(result, "JPEG", quality=92)
    result.name = "photo.jpg"
    return result


def save_photos(entries, data_dir):
    if len(entries) > MAX_PHOTOS:
        raise SecurityError("사진은 최대 3장까지 등록할 수 있습니다.")
    paths = []
    try:
        for entry in entries:
            paths.append(save_image(prepare_upload(entry), data_dir))
    except Exception:
        remove_photos(paths, data_dir)
        raise
    return paths


def remove_photos(paths, data_dir):
    for relative in paths:
        path = image_path(data_dir, relative)
        if path:
            path.unlink(missing_ok=True)


def photo_inputs(prefix, label):
    """사진 업로드는 폼 밖에서 먼저 처리해 영역 변경을 즉시 미리봅니다."""
    uploads = st.file_uploader(label, type=["jpg", "jpeg", "png"], accept_multiple_files=True,
                              key=prefix + "_uploads",
                              help=f"정면·측면 등 최대 3장, 사진당 {MAX_UPLOAD_MB}MB. 선택한 영역이 저장됩니다.")
    if len(uploads) > MAX_PHOTOS:
        st.error("사진은 최대 3장입니다. 불필요한 사진을 제거해 주세요.")
        return None
    if not uploads:
        return []
    hashes = [hashlib.sha256(upload.getvalue()).hexdigest() for upload in uploads]
    if len(set(hashes)) != len(hashes):
        st.error("같은 사진이 중복 선택되었습니다.")
        return None
    images = []
    try:
        for upload in uploads:
            images.append(read_image(upload))
    except SecurityError as exc:
        for image in images:
            image.close()
        st.error(str(exc))
        return None
    try:
        signature = "-".join(hashes)
        if st.session_state.get(prefix + "_signature") != signature:
            st.session_state[prefix + "_signature"] = signature
            st.session_state[prefix + "_boxes"] = {}
            st.session_state[prefix + "_representative"] = 0
            st.session_state[prefix + "_selected"] = 0
        boxes = st.session_state[prefix + "_boxes"]
        columns = st.columns(len(images))
        representative = st.session_state[prefix + "_representative"]
        for index, (column, image) in enumerate(zip(columns, images)):
            with column:
                st.image(image, width=140)
                st.caption(f"사진 {index + 1}" + (" · 대표" if index == representative else "") +
                           (" · 영역 선택됨" if hashes[index] in boxes else ""))
        with st.expander("대표 사진·물건 영역 선택"):
            st.session_state.setdefault(prefix + "_selected", 0)
            selected = st.segmented_control("영역을 지정할 사진", list(range(len(images))),
                format_func=lambda index: f"사진 {index + 1}", key=prefix + "_selected")
            selected = 0 if selected is None else selected
            image = images[selected]
            st.caption("물건 전체가 포함되도록 테두리를 조절한 뒤 ‘선택 영역 사용’을 누르세요.")
            box = boxes.get(hashes[selected], (0, 0, 1, 1))
            frame_box = box if hashes[selected] in boxes else (.04, .04, .96, .96)
            left, top, right, bottom = frame_box
            edited = st_cropper(image, return_type="box", box_color="#1ed760",
                default_coords=(left * image.width, right * image.width, top * image.height, bottom * image.height),
                key=f"{prefix}_crop_{hashes[selected]}_{box}")
            a, b, c = st.columns(3)
            if a.button("선택 영역 사용", key=prefix + "_apply"):
                crop = (edited["left"] / image.width, edited["top"] / image.height,
                        (edited["left"] + edited["width"]) / image.width,
                        (edited["top"] + edited["height"]) / image.height)
                candidate = {"upload": uploads[selected], "crop": crop}
                try:
                    prepare_upload(candidate)
                    boxes[hashes[selected]] = crop
                except SecurityError as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
            if b.button("사진 전체 사용", key=prefix + "_full"):
                boxes.pop(hashes[selected], None)
                st.rerun()
            if c.button("이 사진을 대표로", key=prefix + "_set_representative", disabled=representative == selected):
                st.session_state[prefix + "_representative"] = selected
                st.rerun()
            left, top, right, bottom = box
            with image.crop((int(left * image.width), int(top * image.height),
                             int(right * image.width), int(bottom * image.height))) as preview:
                st.image(preview, width=240, caption="저장·비교에 사용할 영역")
        order = [representative] + [index for index in range(len(uploads)) if index != representative]
        return [{"upload": uploads[index], "crop": boxes.get(hashes[index], (0, 0, 1, 1))} for index in order]
    finally:
        for image in images:
            image.close()
