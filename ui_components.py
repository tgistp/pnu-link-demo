"""본문의 사진 목록·갤러리만 React로 표시하고 권한·업무 처리는 Python에 남깁니다."""
from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components

_catalog=components.declare_component("pnu_catalog",path=str(Path(__file__).parent/"ui/dist"))


def render(data,key):
    event=_catalog(data=data,key=key,default=None)
    if not isinstance(event,dict) or not isinstance(event.get("event"),str) or not 1<=len(event["event"])<=80:
        return None
    if st.session_state.get(key+"_event")==event["event"]:
        return None
    st.session_state[key+"_event"]=event["event"]
    return event
