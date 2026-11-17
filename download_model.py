"""Restore the pinned public model if it was omitted from a shared folder."""
import hashlib
from pathlib import Path
from urllib.request import urlopen,Request

BASE=Path(__file__).resolve().parent/"models/answer-minilm"
ROOT="https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/resolve/2c4055b12046f11709e9df2c122e59ffbdc2f900/"
FILES={
 "model_quantized.onnx":("onnx/model_quantized.onnx","66fc00f5f29afcaff34092e1bdd20008ca3918265a82fb9695a551e510cc4ebc"),
 "tokenizer.json":("tokenizer.json","b60b6b43406a48bf3638526314f3d232d97058bc93472ff2de930d43686fa441"),
 "config.json":("config.json","05b570bff786faa5c4604152aa16f19f77ed6dfc31e47dd0f3dd987078693ac7"),
}
PHOTO_ROOT="https://huggingface.co/Xenova/dinov2-small/resolve/c2bb04a51fab207c420665f1946016107bffc701/"

def prepare_models():
    # 큰 모델은 GitHub에 올리지 않고 고정 버전을 내려받아 체크섬으로 검증합니다.
    targets=[(BASE/name,ROOT+remote,expected) for name,(remote,expected) in FILES.items()]
    targets.append((BASE.parent/"photo-dinov2/vision-int8.onnx",
                    PHOTO_ROOT+"onnx/model_quantized.onnx",
                    "3afdc8bc63b50558d6e5770f5b799bb82455c2311183a2de43803f343a29d917"))
    for target,url,expected in targets:
        target.parent.mkdir(parents=True,exist_ok=True)
        name=target.name
        if target.is_file():
            with target.open("rb") as f:valid=hashlib.file_digest(f,"sha256").hexdigest()==expected
            if valid:
                print("Verified:",name);continue
        temporary=target.with_name(name+".partial")
        try:
            with urlopen(Request(url,headers={"User-Agent":"CampusLostDemo/1.0"}),timeout=90) as response,temporary.open("wb") as f:
                total=0
                while chunk:=response.read(1024*1024):
                    total+=len(chunk)
                    if total>150*1024*1024:raise ValueError("Download size limit")
                    f.write(chunk)
            with temporary.open("rb") as f:actual=hashlib.file_digest(f,"sha256").hexdigest()
            if actual!=expected:raise ValueError("SHA-256 mismatch")
            temporary.replace(target)
            print("Downloaded and verified:",name)
        finally:
            temporary.unlink(missing_ok=True)

if __name__=="__main__":
    prepare_models()
