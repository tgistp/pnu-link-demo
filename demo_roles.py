"""시연 설정과 역할별 연결 수명은 화면 위젯과 분리해 관리합니다."""

import core
import security as sec


def adopt_current_role(state, token, default="recipient"):
    if state.get("demo_active_role"):
        return
    role = (
        "registrant"
        if not core.owner_items(token).empty
        else "recipient"
        if not core.owner_reports(token).empty
        else default
    )
    state["demo_active_role"] = role
    state[f"demo_{role}_session"] = token
    for name in ("issued_code", "issued_otp"):
        state[f"demo_{role}_{name}"] = state.get(name)


def ensure_owner(state, role="recipient", *, enabled=False):
    """등록·신고 작업에 필요한 세션이 없으면 새 확인 코드로 연결합니다."""
    token = state.get("owner_session")
    if token:
        core.owner_items(token)
    else:
        code, token = core.create_owner(state["actor_id"], demo=enabled)
        state["owner_session"] = token
        state["issued_code"] = code
    adopt_current_role(state, token, role)
    if enabled and state["demo_active_role"] != role:
        change_demo_role(state, role, navigate=False, enabled=enabled)
    return state["owner_session"]


def change_demo_role(state, role, item_id=None, *, navigate=True, enabled=False):
    """한 사람이 시연할 수 있도록 등록자와 수령자의 세션을 따로 관리합니다."""
    """Keep both locally created sessions for a one-browser classroom demo."""
    if role not in ("registrant", "recipient"):
        raise sec.SecurityError("역할을 확인해 주세요.")
    current = state.get("owner_session")
    if current:
        adopt_current_role(state, current)
    if item_id is not None:
        if not current:
            raise sec.SecurityError("이 물건의 등록자 연결을 먼저 확인해 주세요.")
        mine = core.owner_items(current)
        if item_id not in mine.id.tolist():
            raise sec.SecurityError("이 물건의 등록자 연결을 먼저 확인해 주세요.")
        state["demo_registrant_session"] = current
        state["demo_active_role"] = "registrant"
    active = state.get("demo_active_role")
    if active:
        for name in ("issued_code", "issued_otp"):
            state[f"demo_{active}_{name}"] = state.get(name)
    target = state.get(f"demo_{role}_session")
    if target == current and active != role:
        target = None
    if not target:
        code, target = core.create_owner(state["actor_id"], demo=enabled)
        state[f"demo_{role}_session"] = target
        state[f"demo_{role}_issued_code"] = code
    core.owner_items(target)
    state["owner_session"] = target
    state["demo_active_role"] = role
    for name in ("issued_code", "issued_otp"):
        value = state.get(f"demo_{role}_{name}")
        if value:
            state[name] = value
        else:
            state.pop(name, None)
    if navigate:
        state["page"] = "분실물 찾기" if role == "recipient" else "등록 내역"


def clear_demo_roles(state):
    """역할별 연결만 지웁니다. 사용자가 켠 시연 설정은 메뉴 이동·재연결에도 유지합니다."""
    for key in list(state):
        if key == "demo_active_role" or key.startswith(
            ("demo_registrant_", "demo_recipient_")
        ):
            del state[key]


def set_demo_mode(state):
    # 표시되지 않는 위젯의 값은 Streamlit이 정리하므로 설정은 별도 일반 키에 보관합니다.
    state["demo_mode"] = bool(state.get("demo_mode_control"))
