import io
import uuid
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from security import SecurityError

MAX_UPLOAD_MB = 25
MAX_IMAGE_PIXELS = 64_000_000
MAX_IMAGE_SIZE = (1600, 1600)
# Some phone JPEGs carry an extra frame. Pillow identifies these files as MPO.
IMAGE_FORMATS = ("JPEG", "PNG", "MPO")


def read_image(upload):
    """파일 종류와 크기를 검증하고 사진 방향과 해상도를 정리합니다."""
    raw = upload.getvalue()
    if not raw or len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        raise SecurityError(f"사진은 {MAX_UPLOAD_MB}MB 이하로 업로드해 주세요.")
    if Path(upload.name).suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise SecurityError("JPG 또는 PNG 사진을 선택해 주세요.")
    if not (raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"\x89PNG\r\n\x1a\n")):
        raise SecurityError("사진 파일 내용을 확인해 주세요.")
    try:
        with Image.open(io.BytesIO(raw), formats=IMAGE_FORMATS) as im:
            if im.width * im.height > MAX_IMAGE_PIXELS:
                raise SecurityError(f"사진은 {MAX_IMAGE_PIXELS // 10_000:,}만 화소 이하로 올려 주세요.")
            im.verify()
        with Image.open(io.BytesIO(raw), formats=IMAGE_FORMATS) as im:
            im.thumbnail(MAX_IMAGE_SIZE)
            return ImageOps.exif_transpose(im).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise SecurityError("사진을 처리할 수 없습니다. 다른 JPG/PNG 파일을 사용해 주세요.") from exc


def save_image(upload, data):
    """습득 사진을 새 JPEG로 변환해 임의 이름으로 저장합니다."""
    if upload is None:
        return ""
    try:
        with read_image(upload) as im:
            folder = Path(data) / "images"
            folder.mkdir(parents=True, exist_ok=True)
            if folder.resolve().parent != Path(data).resolve():
                raise SecurityError("사진 저장 경로를 확인해 주세요.")
            if sum(p.stat().st_size for p in folder.glob("*.jpg")) > 500 * 1024 * 1024:
                raise SecurityError("사진 저장 용량을 확인해 주세요.")
            path = folder / f"{uuid.uuid4().hex}.jpg"
            im.save(path, "JPEG", quality=85)
            return path.relative_to(data).as_posix()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise SecurityError("사진을 처리할 수 없습니다. 다른 JPG/PNG 파일을 사용해 주세요.") from exc
