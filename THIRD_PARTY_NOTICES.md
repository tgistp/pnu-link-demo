# 사진 비교 구현 참고 자료

## React Bits Pulse Heart / AnimatedContent

- 공식 등록 자료: https://reactbits.dev/r/PulseHeart-JS-CSS.json 및 https://reactbits.dev/r/AnimatedContent-JS-CSS.json (2026-11-24 확인).
- Pulse Heart는 본문 관심 저장 버튼에 사용하며 숫자는 표시하지 않습니다. 공식 수축·색상 전환·펄스 동작을 유지하고 아이콘만 앱의 기존 하트 경로로 교체해 별도 아이콘 패키지는 추가하지 않았습니다.
- AnimatedContent는 본문 물건 목록의 등장 동작에 사용합니다. 목록 내부 갱신에서는 재생하지 않습니다. 상세 사진에는 FadeContent를 사용하며 부모 화면은 등장 효과가 끝난 뒤 준비된 화면으로 전환합니다.
- Copyright (c) 2026 David Haz. MIT + Commons Clause. 라이선스 전문은 ui/dist/PULSE_HEART_LICENSE.md 및 ui/dist/ANIMATED_CONTENT_LICENSE.md에 포함합니다.
- 실행 코드와 라이선스는 ui_components.py의 공개 화면 묶음에도 포함됩니다. 외부 CDN이나 API 키는 사용하지 않습니다.

## React Bits FadeContent / GSAP

- FadeContent: https://github.com/DavidHDev/react-bits/blob/cbfda76dcc097f643af20cf18940fc0279dbee25/src/content/Animations/FadeContent/FadeContent.jsx
- 해당 원본을 본문의 물건 목록과 상세 사진 전환에 사용합니다. 시작 화면에는 추가 적용하지 않습니다. 현재 고정 버전은 움직임 줄이기 설정을 지원합니다.
- SpotlightCard는 본문 목록의 빛·테두리 반응에도 사용합니다.
- Copyright (c) 2026 David Haz. MIT + Commons Clause. 전체 이용 조건은 ui/dist/FADE_CONTENT_LICENSE.md에 포함합니다.
- GSAP 3.15.1: https://gsap.com/standard-license . 실행 코드는 앱에 포함하며 외부 CDN이나 API 키를 사용하지 않습니다.

## Streamlit Cropper

- https://github.com/turner-anderson/streamlit-cropper
- streamlit-cropper 0.3.1, MIT.
- 아래 MIT 이용 조건은 이 구성요소에도 적용한다.
- 해당 패키지 LICENSE의 저작권 표기는 Copyright (c) 2018 The Python Packaging Authority이다.

## Same

- 저장소 https://github.com/Ps23102004/same
- 검토 파일 backend/app/retrieval/verify.py
- 참고한 구조는 SIFT 대응점 검색, 비율 검사, RANSAC으로 일관된 대응점 확인이다.
- 이 프로젝트는 이를 정규화 좌표, 상호 대응 검사, 고정 크기 특징 저장에 맞게 재구현했다.
- 원본 저장소의 성능 수치를 이 앱의 성능으로 사용하지 않는다.

MIT License

Copyright (c) 2026 Parth Singh

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## DINO / DINOv2

- https://github.com/facebookresearch/dino/blob/main/eval_image_retrieval.py
- https://github.com/facebookresearch/dinov2
- Apache-2.0. DINOv2 모델 파일과 기존 라이선스는 models 디렉터리에서 관리한다.
- 전체 특징과 부분 특징을 통한 검색 구조를 검토했다. 추가 모델 학습을 수행한 것은 아니다.
