import html
import json
import secrets
import sqlite3
from datetime import datetime, time, timedelta

import folium
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from streamlit_folium import st_folium

import core
import security as sec
from photo_editor import photo_inputs, prepare_upload, save_photos, remove_photos

st.set_page_config(page_title="PNU:LINK", layout="wide", initial_sidebar_state="collapsed")
try:
    core.init_db()
except Exception:
    st.error("저장소를 열지 못했습니다. 프로그램을 다시 실행하거나 로컬 관리자에게 문의해 주세요.")
    st.stop()
PLACES = core.places()
st.session_state.setdefault("actor_id", secrets.token_urlsafe(24))
PAGES = ["처음", "분실물 찾기", "습득물 등록", "분실 신고 · 추천", "등록 내역"]
LABELS = dict(zip(PAGES, ["처음", "물건 찾기", "주운 물건 등록", "분실 신고", "내 등록·인증 내역"]))
if st.session_state.get("page") not in PAGES:
    st.session_state["page"] = "처음"

st.markdown("""<style>
.block-container{max-width:1140px;padding-top:3.7rem}
h1{font-size:1.7rem!important;letter-spacing:-.04em}h2{font-size:1.25rem!important}h3{font-size:1.05rem!important}
.st-key-page{border-bottom:1px solid color-mix(in srgb,currentColor 18%,transparent);padding-bottom:.4rem;margin-bottom:1rem}
.st-key-page [role='radiogroup']{gap:1.4rem}.st-key-page [data-baseweb='radio']>div:first-child,.st-key-page [data-testid='stRadioOption']>div>div:first-child{display:none}
.st-key-page label{padding:.4rem .1rem;border-bottom:2px solid transparent}.st-key-page label:has(input:checked){border-color:#1ed760;font-weight:700}
.st-key-page label:focus-within{outline:2px solid #1ed760;outline-offset:3px}
button[kind='primary']{background:#1ed760!important;border-color:#1ed760!important;color:#06170c!important;border-radius:5px!important}
button[kind='primary']:hover{background:#3ee67b!important}
[class*='st-key-item_']{border:1px solid color-mix(in srgb,currentColor 13%,transparent);background:color-mix(in srgb,currentColor 3%,transparent);padding:1rem;border-radius:7px}
.photo-empty{height:96px;display:flex;align-items:center;justify-content:center;background:color-mix(in srgb,currentColor 7%,transparent);font-size:.8rem;color:inherit}
.item-status{font-size:.75rem;color:#08783b;background:#ddf7e8;padding:3px 7px;border-radius:3px}
.app-brand{font-family:Arial,'Malgun Gothic',sans-serif;font-weight:800;letter-spacing:-.045em!important}.app-brand span{color:#1ed760}
.processing-history{list-style:none;padding:0;margin:.4rem 0}
.processing-history li{border-left:2px solid #3e5948;padding:0 0 1.25rem 1rem;position:relative}
.processing-history li:last-child{padding-bottom:0}
.processing-history li:before{content:"";width:9px;height:9px;background:#1ed760;border-radius:50%;position:absolute;left:-5px;top:6px}
.processing-history strong{display:block;font-weight:600}
.processing-history small{display:block;opacity:.7;margin-top:.25rem}
</style>""", unsafe_allow_html=True)

if st.session_state["page"] == "처음":
    st.markdown("<style>.block-container{max-width:none;padding:0}[data-testid='stHeader']{display:none}</style>", unsafe_allow_html=True)
    landing = components.declare_component("campus_start", path=str(core.BASE / "landing" / "dist"))
    result = landing(key="start_" + str(st.session_state.get("start_generation", 0)), default=None)
    if result in ("lost", "found"):
        st.session_state["page"] = "분실 신고 · 추천" if result == "lost" else "습득물 등록"
        st.session_state["start_generation"] = st.session_state.get("start_generation", 0) + 1
        st.rerun()
    st.stop()


def guard(function, *args, **kwargs):
    """업무 함수의 오류를 화면 메시지로 바꾸고 성공 여부와 결과를 함께 반환합니다."""
    try:
        return True, function(*args, **kwargs)
    except sec.SecurityError as exc:
        st.error(str(exc))
    except Exception:
        # No exception details or user content are sent to the browser.
        try:
            with core.connect() as con:
                sec.event(con, core.now(), "operation_error", outcome="error")
        except sqlite3.Error:
            pass
        st.error("처리를 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.")
    return False, None


def adopt_current_role(token, default="recipient"):
    if st.session_state.get("demo_active_role"):
        return
    role="registrant" if not core.owner_items(token).empty else "recipient" if not core.owner_reports(token).empty else default
    st.session_state["demo_active_role"]=role
    st.session_state[f"demo_{role}_session"]=token
    for name in ("issued_code","issued_otp"):
        st.session_state[f"demo_{role}_{name}"]=st.session_state.get(name)


def ensure_owner(role="recipient"):
    """등록·신고 작업에 필요한 세션이 없으면 새 확인 코드로 연결합니다."""
    token = st.session_state.get("owner_session")
    if token:
        core.owner_items(token)
    else:
        code, token = core.create_owner(st.session_state["actor_id"])
        st.session_state["owner_session"] = token
        st.session_state["issued_code"] = code
    adopt_current_role(token,role)
    if st.session_state["demo_active_role"]!=role:
        change_demo_role(role,navigate=False)
    return st.session_state["owner_session"]


def change_demo_role(role, item_id=None, *, navigate=True):
    """한 사람이 시연할 수 있도록 등록자와 수령자의 세션을 따로 관리합니다."""
    """Keep both locally created sessions for a one-browser classroom demo."""
    if role not in ("registrant","recipient"):
        raise sec.SecurityError("역할을 확인해 주세요.")
    current = st.session_state.get("owner_session")
    if not current:
        raise sec.SecurityError("확인 코드로 먼저 연결해 주세요.")
    adopt_current_role(current)
    if item_id is not None:
        mine = core.owner_items(current)
        if item_id not in mine.id.tolist():
            raise sec.SecurityError("이 물건의 등록자 연결을 먼저 확인해 주세요.")
        st.session_state["demo_registrant_session"] = current
        st.session_state["demo_active_role"] = "registrant"
    active = st.session_state.get("demo_active_role")
    if active:
        for name in ("issued_code", "issued_otp"):
            st.session_state[f"demo_{active}_{name}"] = st.session_state.get(name)
    target = st.session_state.get(f"demo_{role}_session")
    if target==current and active!=role:
        target=None
    if not target:
        code, target = core.create_owner(st.session_state["actor_id"])
        st.session_state[f"demo_{role}_session"] = target
        st.session_state[f"demo_{role}_issued_code"] = code
    core.owner_items(target)
    st.session_state["owner_session"] = target
    st.session_state["demo_active_role"] = role
    for name in ("issued_code", "issued_otp"):
        value = st.session_state.get(f"demo_{role}_{name}")
        if value:
            st.session_state[name] = value
        else:
            st.session_state.pop(name, None)
    if navigate:
        st.session_state["page"] = "분실물 찾기" if role=="recipient" else "등록 내역"


def role_action(role, item_id=None):
    guard(change_demo_role, role, item_id)


def clear_demo_roles():
    for key in list(st.session_state):
        if key.startswith("demo_"):
            del st.session_state[key]


st.markdown('<h1 class="app-brand">PNU<span>:</span>LINK</h1>',unsafe_allow_html=True)
st.caption("부산대학교 양산캠퍼스 분실물")
page = st.radio("메뉴", PAGES, format_func=LABELS.get, horizontal=True, label_visibility="collapsed", key="page")
if page == "처음":
    st.rerun()
if st.session_state.get("owner_session") and not st.session_state.get("demo_active_role"):
    try:adopt_current_role(st.session_state["owner_session"])
    except sec.SecurityError:pass
if st.session_state.get("owner_session"):
    with st.expander("연결·시연 역할"):
        a, b, c = st.columns([2, 1, 1])
        active = st.session_state.get("demo_active_role")
        a.caption("현재 " + ("등록자" if active == "registrant" else "분실자" if active=="recipient" else "연결됨"))
        b.button("등록자 역할로", on_click=role_action, args=("registrant",), disabled=active == "registrant")
        c.button("분실자 역할로", on_click=role_action, args=("recipient",), disabled=active == "recipient")
flash = st.session_state.pop("notice", "")
if flash:
    st.success(flash)
if st.session_state.pop("receipt_completed", False):
    st.toast("수령 완료로 처리했습니다. 물건 찾기 목록에서 제외했습니다.")


def show_access_code(key):
    if not st.session_state.get("issued_code"):
        return
    with st.expander("내역 확인 코드", expanded=False):
        st.caption("내 등록·인증 내역을 다시 열 때 사용하는 짧은 코드입니다.")
        st.code(st.session_state["issued_code"], language=None)
        st.download_button("코드 파일로 저장", st.session_state["issued_code"], "campus-code.txt", "text/plain", key=key+"_savecode")
        if st.button("코드를 보관했어요", key=key+"_hidecode"):
            del st.session_state["issued_code"]
            st.rerun()


def show_pickup_status(record, key):
    """내 수령 인증 상태와 인증 완료 후 공개되는 보관 장소를 표시합니다."""
    from semantic_ai import THRESHOLD
    expired = pd.Timestamp(record.expires_at) <= pd.Timestamp(core.now())
    if record.status == "반환 완료":
        st.success("수령 완료")
        return True
    if expired:
        st.warning("수령 인증 시간이 만료됐습니다. 답변을 다시 확인해 주세요.")
        return False
    if pd.notna(record.verification_score):
        st.caption(f"로컬 AI 의미 유사도 {record.verification_score*100:.1f} / 100 · 통과 기준 {THRESHOLD*100:.0f} (소유 확률 아님)")
    if record.status == "소유 확인 완료" and pd.notna(record.storage):
        st.success("특징 확인 완료 · 아직 수령 전입니다.")
        st.markdown("**수령 위치**")
        st.text(record.storage)
        st.caption("위 장소에서 실제로 물건을 받은 뒤 아래의 수령 완료를 눌러 주세요.")
        known = st.session_state.get("pickup_codes", {}).get(str(record.id))
        if not known and st.session_state.get("recent_pickup_request") == record.id:
            known = st.session_state.get("issued_otp")
        if known:
            with st.expander("수령 인증번호", expanded=st.session_state.get("renewed_pickup_id")==int(record.id)):
                st.code(known, language=None)
        st.caption("번호를 잊었거나 5회 잘못 입력했다면 다시 받으세요. 이전 번호는 사용할 수 없고 유효시간은 늘어나지 않습니다.")
        if st.button("수령번호 다시 받기",key=key+"_renew_otp"):
            ok,new_otp=guard(core.renew_pickup_otp,int(record.id),st.session_state.get("owner_session"))
            if ok:
                st.session_state.setdefault("pickup_codes",{})[str(record.id)]=new_otp
                st.session_state["issued_otp"]=new_otp
                st.session_state["recent_pickup_request"]=int(record.id)
                st.session_state["renewed_pickup_id"]=int(record.id)
                st.rerun()
        show_access_code(key)
        with st.form(key+"_receipt"):
            otp = known or st.text_input("저장한 8자리 수령 인증번호", type="password", max_chars=8)
            actual = st.checkbox("안내된 장소에서 실제로 물건을 받았습니다.")
            done = st.form_submit_button("수령 완료", type="primary")
        if done:
            ok, _ = guard(core.confirm_received, int(record.id), st.session_state.get("owner_session"), otp, received=actual)
            if ok:
                st.session_state["receipt_completed"] = True
                st.session_state.pop("pickup_item", None)
                st.session_state.pop("recent_pickup_request", None)
                st.session_state.setdefault("pickup_codes", {}).pop(str(record.id), None)
                st.rerun()
        return True
    if record.verification_engine == "unavailable":
        st.warning(record.verification_reason)
    elif record.status == "확인 대기":
        st.warning("자동 확인되지 않았습니다. 답변을 다시 입력하거나 등록자의 확인을 기다려 주세요.")
        if record.verification_reason:
            st.caption(record.verification_reason)
    return False


def show_timeline(item_id, token, key):
    with st.expander("처리 이력"):
        ok, events = guard(core.item_timeline, item_id, token)
        if ok:
            rows = []
            for event in events:
                at = pd.Timestamp(event["at"]).strftime("%m.%d %H:%M")
                detail = at + (" · " + event["detail"] if event["detail"] else "")
                rows.append(f"<li><strong>{html.escape(event['stage'])}</strong><small>{html.escape(detail)}</small></li>")
            st.markdown('<ol class="processing-history">' + "".join(rows) + "</ol>", unsafe_allow_html=True)


@st.fragment(run_every="10s")
def notifications():
    """현재 계정에서 확인할 등록·반환 관련 알림을 표시합니다."""
    token = st.session_state.get("owner_session")
    if token:
        try:
            mine = core.owner_items(token)
            unread = mine.loc[mine.picked_up_at.notna() & mine.seen_at.isna()]
            requests = core.pickup_requests(token)
            pending = requests.loc[requests.status.eq("확인 대기") & pd.to_datetime(requests.expires_at).gt(pd.Timestamp(core.now()))]
            if len(unread):
                st.success(f"등록한 물건 {len(unread)}건이 반환 완료됐습니다. 내 등록·인증 내역에서 확인하세요.")
            if len(pending):
                st.info(f"소유 확인을 기다리는 건이 {len(pending)}건 있습니다. 내 등록·인증 내역에서 검토하세요.")
        except sec.SecurityError:
            st.caption("내역 연결이 만료됐습니다. 확인 코드로 다시 연결해 주세요.")


notifications()


def route_input(prefix):
    """사용자가 지나간 캠퍼스 장소를 순서대로 입력받습니다."""
    st.caption("이동 순서대로 선택하세요. ‘없음’은 제외합니다.")
    names = ["없음"] + list(PLACES.index)
    route = []
    count = st.session_state.get(prefix + "_stops", 3)
    for offset in range(0, count, 3):
        cols = st.columns(3)
        for i in range(offset, min(offset + 3, count)):
            selected = cols[i - offset].selectbox(f"{i + 1}번째 장소", names, key=f"{prefix}_{i}")
            if selected != "없음":
                route.append(selected)
    if count < 6 and st.button("장소 추가", key=prefix + "_add"):
        st.session_state[prefix + "_stops"] = count + 1
        st.rerun()
    radius = st.select_slider("경로에서 검색할 거리 (m)", [50, 100, 200], value=100, disabled=not route, key=prefix + "radius")
    st.caption(" → ".join(route) if route else "장소 조건 없음 · 캠퍼스 전체")
    return route, radius


def draw_map(results, route, radius):
    """Folium 지도에 이동 경로와 습득 위치를 그립니다."""
    m = folium.Map(location=PLACES[["lat", "lon"]].mean().tolist(), zoom_start=16)
    geometry_path=core.BASE/"assets/campus/yangsan-buildings.geojson"
    if geometry_path.is_file():
        geometry=json.loads(geometry_path.read_text(encoding="utf-8"))
        for feature in geometry["features"]:
            feature["properties"]["name"]=html.escape(feature["properties"]["name"])
        folium.GeoJson(geometry,name="건물 위치",
            style_function=lambda _:dict(color="#648679",weight=1,fillColor="#17a957",fillOpacity=.06),
            tooltip=folium.GeoJsonTooltip(fields=["code","name"],aliases=["건물 번호","건물명"])).add_to(m)
    for name, p in PLACES.iterrows():
        folium.CircleMarker([p.lat, p.lon], radius=4, color="#638878", tooltip=html.escape(name)).add_to(m)
    if len(route) > 1:
        folium.PolyLine(route, color="#17a957", weight=4).add_to(m)
    for p in route:
        folium.Circle(p, radius=radius, color="#17a957", weight=1, fill=True, fill_opacity=.06).add_to(m)
    for row in results.dropna(subset=["lat", "lon"]).head(200).itertuples():
        folium.Marker([row.lat, row.lon], tooltip=html.escape(row.title), popup=html.escape(f"#{row.id} · {row.place}")).add_to(m)
    bounds=PLACES[["lat","lon"]]
    m.fit_bounds([[float(bounds.lat.min()),float(bounds.lon.min())],[float(bounds.lat.max()),float(bounds.lon.max())]],padding=[18,18],max_zoom=17)
    st_folium(m, height=340, use_container_width=True, returned_objects=[])
    st.caption("OpenStreetMap 건물 중심 좌표를 사용합니다. 경암공학관은 부지 중심이며 경로는 직선 연결입니다. 출입구·실제 보행로 기준의 정밀 길찾기는 아닙니다.")


def report_delete_button(report_id, title, token, key):
    with st.popover("분실 신고 삭제"):
        st.text(f'“{title}” 신고를 삭제할까요?')
        st.caption("이 신고와 추천 연결이 사라집니다. 주운 물건 등록에는 영향을 주지 않습니다.")
        if st.button("삭제하기", key=key, type="primary"):
            ok, _ = guard(core.delete_report, int(report_id), token)
            if ok:
                st.session_state.pop("selected_report", None)
                st.session_state["notice"] = "분실 신고를 삭제했습니다."
                st.rerun(scope="app")


def display_scores(result, key):
    """추천 총점과 항목별 점수를 그래프와 CSV로 제공합니다."""
    if result.empty:
        return
    from matching_charts import PARTS, score_charts
    parts = dict(PARTS)
    total_column = "score"
    if "photo_score" in result:
        parts["photo_score"] = "사진"
        total_column = "combined_score"
    limits = result.attrs["score_limits"]
    scoring = " · ".join(f"{label} 최대 {limits[key]}점" for key, label in parts.items() if limits.get(key))
    if scoring:
        st.caption(f"{scoring}. 추천 점수는 소유 확률이나 측정된 정확도가 아닙니다.")
    ok, plots = guard(score_charts, result)
    if ok and plots:
        total_tab, parts_tab = st.tabs(["추천 점수 비교", "항목별 점수"])
        with total_tab:
            st.image(plots[0], use_container_width=True)
            st.download_button("점수 그래프 PNG 저장", plots[0], "matching-scores.png", "image/png", key=key+"_png")
        with parts_tab:
            st.image(plots[1], use_container_width=True)
        columns = ["id", "title", *parts, total_column]
        table = result[columns].rename(columns={"id": "번호", "title": "물건", **parts, total_column: "총점"})
        if "distance_m" in result:
            table["동선 거리(m)"] = result.distance_m
        st.caption("그래프는 추천 점수가 높은 순서로 최대 10건을 표시합니다.")
        with st.expander("점수 계산 내역"):
            st.dataframe(table.round(1), hide_index=True)
            st.download_button("항목별 점수 CSV 저장", sec.safe_csv(table), "matching-scores.csv", "text/csv", key=key+"_csv")


def cards(results, prefix="search"):
    """습득물 목록을 페이지로 나누고 각 카드에서 수령 인증으로 연결합니다."""
    if results.empty:
        st.info("조건에 맞는 물건이 없습니다.")
        return
    requests = {}
    own_ids = set()
    token = st.session_state.get("owner_session")
    if token:
        try:
            sent = core.pickup_requests(token, received=False)
            requests = {int(r.item_id): r for r in sent.drop_duplicates("item_id", keep="first").itertuples()}
            own_ids = set(core.owner_items(token).id.tolist())
        except sec.SecurityError:
            pass
    total_pages = max(1, (len(results) + 9) // 10)
    page_key = prefix+"_page"
    result_ids = tuple(int(value) for value in results.id)
    if st.session_state.get(prefix+"_page_results") != result_ids:
        st.session_state.pop(page_key,None)
        st.session_state[prefix+"_page_results"] = result_ids
    number = min(max(1, st.session_state.get(page_key, 1)), total_pages)
    for row in results.iloc[(number-1)*10:number*10].itertuples():
        record = requests.get(int(row.id))
        with st.container(key=f"item_{row.id}"):
            a,b,c = st.columns([1,4,1.2])
            with a:
                path = sec.image_path(core.DATA,row.image)
                if path:
                    st.image(str(path),width=135)
                    if row.is_demo_image: st.caption("시연용 사진")
                else:
                    label = "사진 비공개" if not getattr(row, "image_public", 1) else "사진 없음"
                    st.markdown(f'<div class="photo-empty">{label}</div>',unsafe_allow_html=True)
            with b:
                st.text(row.title)
                if prefix=="recommendations":
                    score = getattr(row, "combined_score", row.score)
                    st.markdown(f"**추천 점수 {score:.1f} / {results.attrs.get('score_max',100)}점**")
                st.caption(f"{row.category} · {row.color} · {row.place if row.place!='없음' else '장소 미등록'} · #{row.id}")
                st.text(row.description[:100] + ("…" if len(row.description) > 100 else ""))
                st.caption(f"{pd.Timestamp(row.found_at).strftime('%m.%d %H:%M')} 습득")
                if hasattr(row,"distance_m"):
                    st.caption(f"경로에서 {row.distance_m:.0f}m" if pd.notna(row.distance_m) else "발견 위치 미등록")
                if record and record.status=="소유 확인 완료":st.caption("특징 확인됨 · 수령 대기")
            with c:
                label="수령 위치 보기" if record and record.status=="소유 확인 완료" else "수령 인증"
                if st.button(label,key=f"pickup_{row.id}",type="primary"):
                    st.session_state["pickup_item"]=int(row.id)
            with st.expander("자세히 보기"):
                st.text(row.description)
                photos = core.public_photos(int(row.id))
                if photos:
                    columns = st.columns(len(photos))
                    for index, (column, relative) in enumerate(zip(columns, photos), 1):
                        path = sec.image_path(core.DATA, relative)
                        if path:
                            column.image(str(path), caption=f"사진 {index}", width="stretch")
                if hasattr(row, "photo_similarity"):
                    similarity = f"{row.photo_similarity:.3f}" if pd.notna(row.photo_similarity) else "미비교"
                    st.caption(f"사진 유사도 {similarity} · 사진 점수 {row.photo_score:.1f}점")
                if token and (int(row.id) in own_ids or record is not None):
                    show_timeline(int(row.id), token, f"{prefix}_{row.id}")
            if st.session_state.get("pickup_item") != int(row.id):continue
            if int(row.id) in own_ids:
                st.info("현재 등록자로 연결되어 있습니다. 혼자 시연하려면 분실자 역할로 전환하세요.")
                st.button("분실자 역할로 전환",key=f"switch_recipient_{row.id}",type="primary",on_click=role_action,args=("recipient",int(row.id)))
                continue
            if record and show_pickup_status(record,f"{prefix}_{row.id}"):
                continue
            st.markdown("**물건 확인 질문**")
            st.text(row.verification_question or "공개 사진에 보이지 않는 특징 한 가지를 알려주세요.")
            st.caption("로컬 AI가 답변의 의미를 비교합니다. 표현이 달라도 의미가 유사하면 이 물건 아래에 수령 위치를 안내합니다.")
            with st.form(f"proof_{row.id}"):
                evidence=st.text_input("질문에 대한 답변",max_chars=1000,key=f"answer_{row.id}",type="password")
                note=st.text_input("등록자에게 남길 말 (선택)",max_chars=500)
                submit=st.form_submit_button("답변 확인 · 수령 위치 보기",type="primary")
            if submit:
                def request():return core.request_pickup(int(row.id),ensure_owner(),evidence,note)
                with st.spinner("로컬 AI가 답변 의미를 비교하고 있습니다…"):
                    ok,result=guard(request)
                if ok:
                    st.session_state["issued_otp"]=result[1]
                    st.session_state["recent_pickup_request"]=int(result[0])
                    st.session_state.setdefault("pickup_codes",{})[str(result[0])]=result[1]
                    st.rerun()
            if st.button("닫기",key=f"cancel_{row.id}"):
                st.session_state.pop("pickup_item",None);st.rerun()
    with st.container(horizontal=True, horizontal_alignment="center"):
        st.pagination(total_pages, max_visible_pages=10, width=600, key=page_key)


items = core.read("items")
items = items.loc[items.status.ne("반환 완료")].copy()
snapshot = core.inventory_revision()


@st.fragment(run_every="5s")
def watch_inventory():
    """등록이나 반환으로 목록이 바뀌었는지 확인해 열린 화면을 갱신합니다."""
    if core.inventory_revision() != snapshot:
        st.rerun()


watch_inventory()
# 공개 목록에서는 사용자가 선택한 조건으로 후보를 좁힙니다.
if page == "분실물 찾기":
    st.subheader("주인을 찾고 있는 물건")
    a, b, c = st.columns([3, 1, 1])
    query = a.text_input("물건 이름이나 특징", max_chars=200, placeholder="예: 검정 가죽 지갑")
    category = b.selectbox("종류", ["전체"] + core.CATEGORIES)
    color = c.selectbox("색상", ["전체"] + core.COLORS)
    route, radius, start, end = [], 100, None, None
    with st.expander("장소·시간으로 좁히기"):
        if st.checkbox("이동한 장소로 찾기"):
            names, radius = route_input("search")
            route = core.route_coords(names)
        if st.checkbox("습득 시간 범위 지정"):
            d1 = st.date_input("습득 시작일", core.now().date() - timedelta(days=7))
            d2 = st.date_input("습득 종료일", core.now().date())
            start, end = datetime.combine(d1, time.min), datetime.combine(d2, time.max)
    if start and start > end:
        st.error("날짜 범위를 확인해 주세요.")
    else:
        result = core.search(items, category, color, query, route, radius, start, end).sort_values("found_at", ascending=False)
        st.write(f"{len(result)}건")
        with st.expander("지도에서 보기", expanded=bool(route)):
            draw_map(result, route, radius)
        if route:
            st.subheader("동선 매칭 점수")
            with st.expander("점수 자세히 보기"):
                display_scores(result, "route_scores")
        cards(result)
        public = ["id", "title", "category", "color", "place", "found_at", "status"]
        st.download_button("검색 결과 저장", sec.safe_csv(result[public]), "search_results.csv", "text/csv")

# 습득물 등록 화면은 공개 설명과 소유 확인용 비공개 정보를 따로 받습니다.
elif page == "습득물 등록":
    st.subheader("주운 물건 등록")
    show_access_code("found")
    st.caption("사진과 기본 정보를 입력해 주세요. 별표(*)는 필수 항목입니다.")
    found_photos = photo_inputs("found_photos", "사진 (선택 · 최대 3장)")
    with st.container():
        left, right = st.columns(2, gap="large")
        with left:
            title = st.text_input("물건 이름 *", max_chars=80)
            a, b = st.columns(2)
            category = a.selectbox("종류", core.CATEGORIES)
            color = b.selectbox("색상", core.COLORS)
            description = st.text_area("공개할 특징 *", max_chars=1000,
                placeholder="예: 투명 케이스, 모서리 흠집, 둥근 스티커")
        with right:
            place = st.selectbox("발견 장소", ["없음"] + list(PLACES.index))
            a, b = st.columns(2)
            date = a.date_input("발견 날짜", core.now().date())
            clock = b.time_input("발견 시각", core.now().time(), key="found_clock")
            storage = st.text_input("보관 장소 / 반환 안내 *", max_chars=250)
            with st.expander("소유 확인 질문·공개 설정"):
                question = st.text_input("소유 확인 질문 (선택)", max_chars=200, placeholder="예: 지갑 안쪽에는 어떤 표시가 있나요?")
                private = st.text_input("질문의 정답 (비공개)", max_chars=500, placeholder="예: 안쪽 별 모양",type="password")
                image_public = st.checkbox("사진을 검색 목록에 공개", value=True,
                    help="이름·연락처 등이 있으면 해제하세요. 비공개 사진은 서버 내부 비교에만 사용합니다.")
                manual_verification = st.checkbox("소유 확인은 등록자가 최종 승인",
                    help="AI를 통과해도 등록자 승인 전에는 보관 장소가 공개되지 않습니다.")
                st.caption("질문·정답을 모두 비워 두면 등록자가 직접 확인합니다.")
        submitted = st.button("습득물 등록", type="primary")
    if submitted:
        def register():
            token = ensure_owner("registrant")
            # Validate required fields before parsing or writing the uploaded file.
            sec.clean(title, "물건 이름", 80, 1)
            sec.clean(description, "공개 특징", 1000, 1)
            sec.clean(storage, "보관 장소", 250, 1)
            if bool(question.strip()) != bool(private.strip()):
                raise sec.SecurityError("소유 확인 질문과 비공개 정답을 함께 입력해 주세요.")
            if found_photos is None:
                raise sec.SecurityError("선택한 사진을 확인해 주세요.")
            paths = save_photos(found_photos, core.DATA)
            try:
                return core.insert("items", dict(title=title, category=category, color=color, description=description,
                    place=place, found_at=datetime.combine(date, clock).isoformat(), storage=storage, private_hint=private,
                    verification_question=question, image=paths[0] if paths else "", image_public=image_public,
                    manual_verification=manual_verification), owner_session=token, photo_paths=paths or None)
            except Exception:
                remove_photos(paths, core.DATA)
                raise
        ok, iid = guard(register)
        if ok:
            st.session_state["notice"] = f"#{iid} 등록했습니다. 확인 코드를 보관해 주세요."
            st.rerun()

# 저장한 분실 신고를 기준으로 기본 조건 점수와 사진 점수를 합산합니다.
elif page == "분실 신고 · 추천":
    st.subheader("분실 신고")
    show_access_code("report")
    st.caption("기억나는 특징과 사진을 남기면 비슷한 습득물을 추천합니다.")
    report_photos = photo_inputs("report_photos", "분실물 사진 (선택 · 최대 3장)")
    with st.expander("이동 동선 추가 (선택)"):
        names, radius = route_input("report")
    with st.container():
        title = st.text_input("분실물 이름 *", max_chars=80)
        a, b = st.columns(2)
        category = a.selectbox("분실물 종류", core.CATEGORIES)
        color = b.selectbox("분실물 색상", core.COLORS)
        keywords = st.text_input("기억나는 특징", max_chars=200,
            placeholder="예: 투명 케이스, 모서리 흠집, 둥근 스티커",
            help="눈에 보이는 단서를 적고, 여러 개라면 쉼표로 나누세요. 공개 등록 글과 표현이 같거나 AI가 의미가 비슷하다고 판단한 단서의 비율만큼 최대 20점을 받습니다.")
        a, b = st.columns(2)
        d1 = a.date_input("분실 추정 시작 날짜", core.now().date())
        t1 = a.time_input("분실 추정 시작 시각", time.min)
        d2 = b.date_input("분실 추정 종료 날짜", core.now().date())
        t2 = b.time_input("분실 추정 종료 시각", core.now().time(), key="report_end_clock")
        sent = st.button("분실 신고 저장", type="primary")
    if sent:
        def report():
            token = ensure_owner()
            values = dict(title=title, category=category, color=color, keywords=keywords,
                start_at=datetime.combine(d1,t1).isoformat(), end_at=datetime.combine(d2,t2).isoformat(),
                route=json.dumps(names, ensure_ascii=False), radius=radius)
            if report_photos is None:
                raise sec.SecurityError("선택한 사진을 확인해 주세요.")
            if report_photos:
                from photo_match import embed_uploads
                values["photo_embedding"] = embed_uploads([prepare_upload(entry) for entry in report_photos])
            return core.insert("reports", values, owner_session=token)
        with st.spinner("분실 신고를 저장하는 중입니다…"):
            ok, _ = guard(report)
        if ok:
            st.session_state["notice"] = "분실 신고를 저장했습니다."
            st.rerun()
    if st.session_state.get("owner_session"):
        ok, reports = guard(core.owner_reports, st.session_state["owner_session"])
        if ok and not reports.empty:
            indexed = reports.set_index("id")
            rid = st.selectbox("내 분실 신고", list(indexed.index), format_func=lambda i: f"#{i} {indexed.loc[i,'title']}", key="selected_report")
            r = indexed.loc[rid]
            report_delete_button(rid, r.title, st.session_state["owner_session"], f"delete_report_{rid}")
            result = core.search(items.loc[items.category.eq(r.category)], r.category, r.color, r.keywords,
                core.route_coords(json.loads(r.route)), int(r.radius), r.start_at, strict=False)
            st.subheader("비슷한 습득물")
            st.caption("입력한 조건과 사진이 비슷한 순서입니다.")
            if isinstance(r.photo_embedding, bytes) and not result.empty:
                from photo_match import MAX_CANDIDATES, PHOTO_WEIGHT, rerank
                with st.spinner("습득물 사진을 비교하고 있습니다…"):
                    ok, ranked = guard(rerank, result, r.photo_embedding, core.DATA, report_color=r.color,
                                       image_lookup=core.matching_photo_sets(result.head(MAX_CANDIDATES).id))
                if ok and ranked.photo_similarity.notna().any():
                    result = ranked
                elif ok:
                    st.info("사진을 비교할 수 있는 후보가 없어 기존 순위를 표시합니다.")
            with st.expander("추천 기준·점수 자세히 보기"):
                st.caption("같은 종류의 반환 전 습득물 중 분실 시작 시각 이후 발견된 후보입니다. 동선은 순위 보조 기준이며 반경 밖이나 위치 미등록 후보도 남깁니다.")
                if "photo_similarity" in result:
                    st.caption("기존 상위 30건을 사진으로 비교해 최대 20점을 더합니다. 여러 사진 중 가장 잘 맞는 조합을 사용합니다. 미비교 사진은 0점입니다.")
                    if not result.attrs["photo_details_available"]:
                        st.caption("이전 사진은 전체 외형으로 비교합니다. 새 신고에는 세부 특징 비교도 적용됩니다.")
                    if result.attrs["photo_geometry_available"]:
                        st.caption("충분한 세부 대응점이 있으면 사진 점수를 보강합니다.")
                    if "base_rank" in result:
                        comparison = result[["title", "base_rank", "score", "photo_similarity", "combined_score"]].copy()
                        comparison.insert(0, "반영 후 순위", range(1, len(comparison) + 1))
                        st.dataframe(comparison.rename(columns={
                            "title": "물건", "base_rank": "기존 순위", "score": "기존 점수",
                            "photo_similarity": "사진 유사도", "combined_score": "합산 점수",
                        }).round(2), hide_index=True)
                display_scores(result, "recommendation_scores")
            cards(result, "recommendations")
        elif ok:
            st.info("저장한 분실 신고가 없습니다.")

# 확인 코드에 연결된 사용자만 자신의 등록·인증 내역을 봅니다.
else:
    st.subheader("내 등록·인증 내역")
    show_access_code("records")
    with st.expander("확인 코드로 연결", expanded=not bool(st.session_state.get("owner_session"))):
        st.caption("새 코드의 대소문자·띄어쓰기·하이픈은 자동 정리합니다. 이전 버전의 URL형 코드는 대소문자를 구분합니다.")
        with st.form("login", clear_on_submit=True):
            entered = st.text_input("확인 코드", type="password", key="login_code", max_chars=128)
            load = st.form_submit_button("내역 불러오기")
        if load:
            ok, token = guard(core.authenticate_owner, entered, st.session_state["actor_id"])
            if ok:
                clear_demo_roles()
                st.session_state["owner_session"] = token
                st.session_state.pop("issued_code", None)
                st.rerun()
        st.caption("코드 없이 등록된 이전 물건은 이 컴퓨터의 recover_registration.py로 연결할 수 있습니다.")
    token = st.session_state.get("owner_session")
    if token and st.button("내역 연결 해제"):
        tokens = {token, st.session_state.get("demo_registrant_session"), st.session_state.get("demo_recipient_session")}
        for saved in tokens - {None}:
            guard(core.logout, saved)
        clear_demo_roles()
        for key in ("owner_session", "issued_code", "issued_otp"):
            st.session_state.pop(key, None)
        st.rerun()

    @st.fragment(run_every="10s")
    def my_records():
        token = st.session_state.get("owner_session")
        if not token:
            st.info("확인 코드로 연결하면 등록한 물건과 수령 인증 내역을 볼 수 있습니다.")
            return
        ok, mine = guard(core.owner_items, token)
        if not ok:
            return
        ok, received = guard(core.pickup_requests, token)
        if not ok:
            return
        st.caption("앱이 열려 있는 동안 10초마다 갱신됩니다. 연결은 1시간 뒤 만료됩니다.")
        record_kind = st.segmented_control("내역 종류", ["주운 물건", "분실 신고", "수령 인증"],
                                          default="주운 물건", key="record_kind")
        ok, reports = guard(core.owner_reports, token)
        if ok and not reports.empty and record_kind == "분실 신고":
            st.subheader("내 분실 신고")
            for report in reports.itertuples():
                with st.container(border=True):
                    st.text(f"#{report.id} {report.title}")
                    st.caption(f"{report.category} · {report.color}")
                    report_delete_button(report.id, report.title, token, f"record_delete_report_{report.id}")
        if record_kind == "분실 신고" and ok and reports.empty:
            st.info("등록한 분실 신고가 없습니다.")
        if record_kind == "주운 물건" and mine.empty:
            st.info("등록한 주운 물건이 없습니다.")
        if not mine.empty and record_kind == "주운 물건":
            st.subheader("내가 등록한 주운 물건")
        for row in (mine if record_kind == "주운 물건" else mine.iloc[:0]).itertuples():
            with st.container(border=True):
                st.text(f"#{row.id} {row.title}")
                if pd.notna(row.picked_up_at):
                    st.success("등록하신 물건이 반환 완료됐습니다.")
                    st.caption(f"{row.picked_up_at.replace('T',' ')}")
                    if row.pickup_note:
                        st.text(row.pickup_note)
                    if pd.isna(row.seen_at) and st.button("알림 확인", key=f"seen_{row.id}"):
                        guard(core.acknowledge_pickup, int(row.id), token)
                        st.rerun()
                else:
                    st.caption(row.status)
                st.text("보관 안내: " + row.storage)
                show_timeline(int(row.id), token, f"owner_{row.id}")
                with st.expander("사진 공개·승인 설정"):
                    with st.form(f"privacy_{row.id}"):
                        public = st.checkbox("사진 공개", value=bool(row.image_public), key=f"public_{row.id}")
                        manual = st.checkbox("등록자 최종 승인 필요", value=bool(row.manual_verification), key=f"manual_{row.id}")
                        save_privacy = st.form_submit_button("설정 저장")
                    if save_privacy:
                        ok, _ = guard(core.update_item_privacy, int(row.id), token, public, manual)
                        if ok:
                            st.rerun()
                if row.private_hint:
                    with st.expander("비공개 특징"):
                        st.text(row.private_hint)
                for request in received.loc[received.item_id.eq(row.id) & received.status.isin(["확인 대기", "소유 확인 완료"])].itertuples():
                    if pd.Timestamp(request.expires_at) <= pd.Timestamp(core.now()):
                        continue
                    st.markdown("**소유 확인 접수**" if request.status == "확인 대기" else "**소유 확인 완료 · 수령 대기**")
                    st.text(request.evidence)
                    if request.status == "확인 대기":
                        a, b = st.columns(2)
                        if a.button("소유 확인 승인 · 장소 안내", key=f"disclose_{request.id}", type="primary"):
                            ok, _ = guard(core.approve_ownership, int(request.id), token)
                            if ok:
                                st.rerun()
                        if b.button("소유 근거 불일치 · 반려", key=f"deny_{request.id}"):
                            ok, _ = guard(core.reject_pickup, int(request.id), token)
                            if ok:
                                st.rerun()
                        continue
                    with st.form(f"approve_{request.id}"):
                        otp = st.text_input("수령자가 제시한 8자리 인증번호", type="password", max_chars=8)
                        checked = st.checkbox("현장에서 소유 특징을 확인했고 실제로 물건을 전달했습니다.")
                        approve = st.form_submit_button("확인 후 반환 완료", type="primary")
                        reject = st.form_submit_button("소유 근거 불일치 · 반려")
                    if approve:
                        ok, _ = guard(core.complete_pickup, int(request.id), token, otp, handoff=checked)
                        if ok:
                            st.rerun()
                    if reject:
                        ok, _ = guard(core.reject_pickup, int(request.id), token)
                        if ok:
                            st.rerun()
        ok, sent = guard(core.pickup_requests, token, received=False)
        if ok and sent.empty and record_kind == "수령 인증":
            st.info("수령 인증 내역이 없습니다.")
        if ok and not sent.empty and record_kind == "수령 인증":
            st.subheader("내 수령 인증")
            for r in sent.itertuples():
                status = "유효시간 만료" if r.status in ("확인 대기", "소유 확인 완료") and pd.Timestamp(r.expires_at) <= pd.Timestamp(core.now()) else r.status
                st.text(f"#{r.item_id} {r.title} · {status}")
                with st.container(border=True):
                    show_pickup_status(r,f"record_{r.id}")
                    show_timeline(int(r.item_id), token, f"recipient_{r.id}")
    my_records()

with st.expander("사용 중 불편한 점 남기기"):
    st.caption("이름·연락처·확인 코드 등을 제외하고 적어 주세요. 의견은 공개되지 않습니다.")
    with st.form("feedback", clear_on_submit=True):
        feedback_message = st.text_area("이용 의견", max_chars=1000)
        send_feedback = st.form_submit_button("의견 저장")
    if send_feedback:
        ok, _ = guard(core.submit_feedback, st.session_state["actor_id"], feedback_message)
        if ok:
            st.success("의견을 저장했습니다.")
