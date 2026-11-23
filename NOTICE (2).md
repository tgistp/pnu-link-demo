# DINOv2 small photo ranking

Base model: [Meta DINOv2 small](https://huggingface.co/facebook/dinov2-small), Apache-2.0.
ONNX conversion: [Xenova/dinov2-small](https://huggingface.co/Xenova/dinov2-small).
Pinned revision: `c2bb04a51fab207c420665f1946016107bffc701`.

`vision-int8.onnx` is the upstream `onnx/model_quantized.onnx`, renamed locally.
SHA-256: `3afdc8bc63b50558d6e5770f5b799bb82455c2311183a2de43803f343a29d917`.

Inference runs on the local CPU. The app compares normalized CLS embeddings.
The ranking weight is experimental and has not been calibrated against a real lost-and-found photo dataset.
