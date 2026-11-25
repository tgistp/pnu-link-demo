"""공개 화면 주소를 Streamlit URL 바인딩과 연결합니다. 인증 정보는 URL에 넣지 않습니다."""

import re
from pathlib import Path
import streamlit as st

ROUTES = {
    "처음": "",
    "분실물 찾기": "find",
    "습득물 등록": "found",
    "분실 신고 · 추천": "lost",
    "등록 내역": "records",
    "등록 완료": "complete",
}
PAGES = {route: page for page, route in ROUTES.items()}
SEARCH_KEYS = [
    "search_query",
    "search_category",
    "search_color",
    "search_route_enabled",
    "search_time_enabled",
    *(f"search_{index}" for index in range(6)),
    "searchradius",
    "search_stops",
    "search_startdate",
    "search_enddate",
]

# v0.35: URL 방문 기록과 입력을 막지 않는 본문 전환을 같은 컨트롤러에서 관찰합니다.
_history = st.components.v2.component(
    "pnu_browser_history",
    js=Path(__file__).with_name("browser_motion.js").read_text(encoding="utf-8"),
)


def decode(route):
    if not isinstance(route, str) or len(route) > 80:
        return "처음", None
    if route in PAGES:
        return PAGES[route], None
    match = re.fullmatch(r"item/([1-9][0-9]{0,9})", route)
    if match and int(match[1]) <= 2_147_483_647:
        return "물건 상세", int(match[1])
    return "처음", None


def current_route():
    if st.session_state.get("page") == "물건 상세":
        item = st.session_state.get("detail_item")
        if type(item) is int and 0 < item <= 2_147_483_647:
            return f"item/{item}"
        return "find"
    return ROUTES.get(st.session_state.get("page"), "")


def change_page(target):
    """버튼과 브라우저 뒤로가기가 같은 검색 조건·내역 복원 규칙을 사용합니다."""
    if st.session_state.get("page") == "분실물 찾기":
        st.session_state["saved_search_filters"] = {
            key: st.session_state[key] for key in SEARCH_KEYS if key in st.session_state
        }
        st.session_state["restore_search_filters"] = True
    if target == "등록 내역" and st.session_state.get("page") == "물건 상세":
        if st.session_state.get("detail_return_kind"):
            st.session_state["record_kind"] = st.session_state["detail_return_kind"]
    st.session_state["page"] = target


def read_address():
    target, item = decode(st.session_state.get("route", ""))
    previous = st.session_state.get("page", "분실물 찾기")
    if item is not None:
        if previous != "물건 상세":
            st.session_state["detail_return_page"] = (
                previous if previous != "처음" else "분실물 찾기"
            )
        if item != st.session_state.get("detail_item"):
            st.session_state["detail_recommendation"] = None
        st.session_state["detail_item"] = item
    change_page(target)


def mount():
    """항상 존재하는 URL 바인딩 위젯이 뒤로·앞으로가기를 처리하며 화면에서는 숨깁니다."""
    event = _history(key="browser_history", height=1, on_navigate_change=lambda: None)
    navigation = event.get("navigate")
    if isinstance(navigation, dict) and isinstance(navigation.get("route"), str):
        st.session_state["route"] = navigation["route"]
        read_address()
    if "route" not in st.session_state:
        address = st.query_params.get("route")
        st.session_state["route"] = address if address is not None else current_route()
        if address is not None:
            read_address()
    elif st.session_state.get("route") != st.session_state.get(
        "_rendered_route"
    ) and current_route() == st.session_state.get("_rendered_route"):
        # 브라우저 방문 기록이 복원한 위젯 값은 콜백 없이 전달될 수도 있습니다.
        read_address()
    elif current_route() != st.session_state.get("_rendered_route"):
        # 앱 내부 이동은 위젯이 만들어지기 전에 URL 상태로 옮깁니다.
        st.session_state["route"] = current_route()
    st.text_input(
        "현재 화면 주소",
        key="route",
        bind="query-params",
        on_change=read_address,
        max_chars=80,
    )
    route = current_route()
    if route != st.session_state.get("_rendered_route"):
        st.session_state["view_generation"] = (
            st.session_state.get("view_generation", 0) + 1
        )
    st.session_state["_rendered_route"] = route
    return st.session_state.get("view_generation", 0)
