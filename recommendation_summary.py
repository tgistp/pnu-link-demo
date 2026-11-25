"""점수의 의미를 과장하지 않고 추천 근거를 짧게 표시합니다."""

import math


def reasons(row):
    clues = []
    if getattr(row, "color_score", 0) > 0:
        clues.append("등록 색상 일치")
    feature = getattr(row, "keyword_score", 0)
    if feature > 0:
        clues.append(f"특징 {feature:.0f}/20점")
    distance = getattr(row, "distance_m", None)
    if distance is not None and math.isfinite(distance):
        clues.append(f"동선에서 {distance:.0f}m")
    photo = getattr(row, "photo_similarity", None)
    if photo is not None and math.isfinite(photo):
        clues.append(f"사진 {row.photo_score:.1f}/20점")
    return " · ".join(clues[:3]) or "종류와 발견 시각 조건에 맞는 후보"


def photo_comparison_notice(results):
    """근소한 차이를 소유 확률이나 확정 판정으로 표현하지 않습니다."""
    if "photo_score" not in results or "photo_similarity" not in results:
        return ""
    compared = results.loc[results.photo_similarity.notna()].sort_values(
        "photo_score", ascending=False
    )
    if len(compared) < 2:
        return (
            "사진 비교 가능한 후보가 1건뿐입니다. 실제 특징도 확인해 주세요."
            if len(compared)
            else ""
        )
    gap = float(compared.iloc[0].photo_score - compared.iloc[1].photo_score)
    if results.attrs.get("photo_fallback"):
        return "전체 물건의 세부 대응만으로 후보를 구분하기 어려워 기존 외형 비교를 사용했습니다. 사진 점수만으로 판단하지 말고 로고·무늬도 확인해 주세요."
    if gap < 1:
        return f"사진 상위 두 후보의 차이가 {gap:.2f}점으로 작습니다. 사진만으로 구분하기 어려우니 로고·무늬·흠집도 확인해 주세요."
    return ""
