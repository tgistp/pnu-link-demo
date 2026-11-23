"""Pandas 검색 및 캠퍼스 규모의 경로 거리 계산. 좌표는 (위도, 경도)."""
import math
import hashlib
import secrets
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import pandas as pd
import security as sec
from semantic_ai import judge_answer,ModelUnavailable
from verification import compact, contradicts

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("CAMPUS_DATA_DIR", str(BASE / "data")))
CATEGORIES = ["지갑", "전자기기", "카드·학생증", "가방", "의류", "우산", "기타"]
COLORS = ["검정", "흰색", "회색", "파랑", "빨강", "초록", "갈색", "노랑", "분홍", "보라", "베이지", "민트", "기타"]
KST = timezone(timedelta(hours=9))
ANSWER_POLICY = "typed-facts-v0.16"


def now():
    return datetime.now(KST).replace(tzinfo=None, microsecond=0)


@contextmanager
def connect():
    """SQLite 연결과 트랜잭션의 종료를 관리합니다."""
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DATA / "campus.db", timeout=15)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        with con:
            yield con
    finally:
        con.close()


def init_db():
    """처음 실행할 때 테이블을 만들고 기존 DB에 필요한 열을 추가합니다."""
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
            color TEXT NOT NULL, description TEXT NOT NULL, place TEXT NOT NULL,
            lat REAL, lon REAL, found_at TEXT NOT NULL,
            storage TEXT NOT NULL, image TEXT NOT NULL DEFAULT '',
            private_hint TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '습득물 등록');
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
            color TEXT NOT NULL, keywords TEXT NOT NULL, start_at TEXT NOT NULL,
            end_at TEXT NOT NULL, route TEXT NOT NULL, radius INTEGER NOT NULL, photo_embedding BLOB);
        CREATE TABLE IF NOT EXISTS item_photos (
            item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 2),
            image TEXT NOT NULL, PRIMARY KEY(item_id,position));
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id),
            alias TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '접수');
        CREATE TABLE IF NOT EXISTS registrations (
            item_id INTEGER PRIMARY KEY REFERENCES items(id), code_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pickups (
            id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL UNIQUE REFERENCES items(id),
            note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, seen_at TEXT);
        CREATE TABLE IF NOT EXISTS owners (code_hash TEXT PRIMARY KEY, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS owner_aliases (
            alias_hash TEXT PRIMARY KEY, owner_hash TEXT NOT NULL REFERENCES owners(code_hash));
        CREATE TABLE IF NOT EXISTS owner_sessions (
            token_hash TEXT PRIMARY KEY, owner_hash TEXT NOT NULL REFERENCES owners(code_hash),
            expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS report_owners (
            report_id INTEGER PRIMARY KEY REFERENCES reports(id), owner_hash TEXT NOT NULL REFERENCES owners(code_hash));
        CREATE TABLE IF NOT EXISTS pickup_requests (
            id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id),
            requester_hash TEXT NOT NULL REFERENCES owners(code_hash), evidence TEXT NOT NULL,
            note TEXT NOT NULL, otp_hash TEXT NOT NULL, otp_salt TEXT NOT NULL,
            expires_at TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT '확인 대기');
        CREATE TABLE IF NOT EXISTS attempts (bucket TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS attempts_lookup ON attempts(bucket,created_at);
        CREATE TABLE IF NOT EXISTS security_events (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, action TEXT NOT NULL, subject_id INTEGER, outcome TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY, message TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS favorites (
            owner_hash TEXT NOT NULL REFERENCES owners(code_hash),
            item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL, PRIMARY KEY(owner_hash,item_id));
        CREATE TABLE IF NOT EXISTS report_views (
            report_id INTEGER PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
            last_item_id INTEGER NOT NULL);
        """)
        # 기존 신고에는 현재 목록을 기준점으로 잡아 과거 물건을 새 알림으로 만들지 않습니다.
        con.execute("INSERT OR IGNORE INTO report_views SELECT id,(SELECT COALESCE(MAX(id),0) FROM items) FROM reports")
        con.execute("INSERT OR IGNORE INTO owners SELECT DISTINCT code_hash, ? FROM registrations", (now().isoformat(),))
        con.commit()
        # 기존 버전의 좌표 필수 제약만 해제합니다. 등록물과 반환 요청은 유지합니다.
        columns = {r[1]: r for r in con.execute("PRAGMA table_info(items)")}
        if columns["lat"][3] or columns["lon"][3]:
            backup = sqlite3.connect(DATA / "campus-before-optional-location.db")
            try:
                con.backup(backup)
            finally:
                backup.close()
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute("BEGIN IMMEDIATE")
            con.execute("""CREATE TABLE items_optional_location (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
                color TEXT NOT NULL, description TEXT NOT NULL, place TEXT NOT NULL,
                lat REAL, lon REAL, found_at TEXT NOT NULL,
                storage TEXT NOT NULL, image TEXT NOT NULL DEFAULT '',
                private_hint TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '습득물 등록')""")
            fields = "id,title,category,color,description,place,lat,lon,found_at,storage,image,private_hint,status"
            con.execute(f"INSERT INTO items_optional_location ({fields}) SELECT {fields} FROM items")
            con.execute("DROP TABLE items")
            con.execute("ALTER TABLE items_optional_location RENAME TO items")
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("데이터 연결을 확인할 수 없어 변경을 취소했습니다.")
        if "is_demo_image" not in {r[1] for r in con.execute("PRAGMA table_info(items)")}:
            con.execute("ALTER TABLE items ADD COLUMN is_demo_image INTEGER NOT NULL DEFAULT 0")
        if "verification_question" not in {r[1] for r in con.execute("PRAGMA table_info(items)")}:
            con.execute("ALTER TABLE items ADD COLUMN verification_question TEXT NOT NULL DEFAULT ''")
        for field, default in {"image_public": 1, "manual_verification": 0}.items():
            if field not in {r[1] for r in con.execute("PRAGMA table_info(items)")}:
                con.execute(f"ALTER TABLE items ADD COLUMN {field} INTEGER NOT NULL DEFAULT {default}")
        if "demo_key" not in {r[1] for r in con.execute("PRAGMA table_info(items)")}:
            con.execute("ALTER TABLE items ADD COLUMN demo_key TEXT")
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS items_demo_key ON items(demo_key)")
        if "photo_embedding" not in {r[1] for r in con.execute("PRAGMA table_info(reports)")}:
            con.execute("BEGIN IMMEDIATE")
            if "photo_embedding" not in {r[1] for r in con.execute("PRAGMA table_info(reports)")}:
                con.execute("ALTER TABLE reports ADD COLUMN photo_embedding BLOB")
        request_columns={r[1] for r in con.execute("PRAGMA table_info(pickup_requests)")}
        for field,kind in {"verification_score":"REAL","verification_engine":"TEXT","verification_reason":"TEXT"}.items():
            if field not in request_columns:con.execute(f"ALTER TABLE pickup_requests ADD COLUMN {field} {kind}")
        policy=con.execute("SELECT value FROM app_metadata WHERE key='answer_policy'").fetchone()
        if not policy or policy[0]!=ANSWER_POLICY:
            # Previously exposed places cannot be recalled, but stale automatic
            # approvals must not authorize new handovers under the old policy.
            con.execute("""UPDATE pickup_requests SET status='확인 대기',verification_score=NULL,
                verification_reason='인증 기준이 업데이트되었습니다. 답변을 다시 확인해 주세요.'
                WHERE status='소유 확인 완료' AND verification_engine='local-minilm-onnx' AND expires_at>?""",
                (now().isoformat(),))
            con.execute("INSERT OR REPLACE INTO app_metadata VALUES ('answer_policy',?)",(ANSWER_POLICY,))
        con.commit()
        version=hashlib.sha256((BASE/"places.csv").read_bytes()).hexdigest()
        previous=con.execute("SELECT value FROM app_metadata WHERE key='place_coordinates'").fetchone()
        if not previous or previous[0]!=version:
            if con.execute("SELECT 1 FROM items LIMIT 1").fetchone() and not (DATA/"campus-before-coordinate-update.db").exists():
                backup=sqlite3.connect(DATA/"campus-before-coordinate-update.db")
                try:con.backup(backup)
                finally:backup.close()
            for name,p in places().iterrows():
                con.execute("UPDATE items SET lat=?,lon=? WHERE place=?",(float(p.lat),float(p.lon),name))
            con.execute("INSERT OR REPLACE INTO app_metadata VALUES ('place_coordinates',?)",(version,))


def inventory_revision():
    with connect() as con:
        return (tuple(con.execute("SELECT id,status,image_public,manual_verification FROM items ORDER BY id").fetchall()),
                tuple(con.execute("SELECT id,status FROM pickup_requests ORDER BY id").fetchall()))


def read(table):
    if table != "items":
        raise sec.SecurityError("비공개 자료는 인증 후 조회해야 합니다.")
    with connect() as con:
        return pd.read_sql_query("""SELECT id,title,category,color,description,place,lat,lon,
            found_at,CASE WHEN image_public=1 THEN image ELSE '' END AS image,
            status,is_demo_image,verification_question,image_public FROM items ORDER BY id DESC""", con)


def matching_images(item_ids):
    """비공개 사진 경로는 공개 목록에서 제외하고 서버 내부의 비교에만 제공합니다."""
    ids = [sec.positive_id(value) for value in item_ids]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with connect() as con:
        return dict(con.execute(f"SELECT id,image FROM items WHERE id IN ({placeholders})", ids))


def matching_photo_sets(item_ids):
    """기존 한 장짜리 등록과 새 다중 사진 등록을 같은 형식으로 비교합니다."""
    ids = [sec.positive_id(value) for value in item_ids]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with connect() as con:
        main = dict(con.execute(f"SELECT id,image FROM items WHERE id IN ({placeholders})", ids))
        rows = con.execute(f"SELECT item_id,image FROM item_photos WHERE item_id IN ({placeholders}) ORDER BY position", ids).fetchall()
    photos = {item_id: [] for item_id in main}
    for item_id, image in rows:
        photos[item_id].append(image)
    return {item_id: photos[item_id] or ([image] if image else []) for item_id, image in main.items()}


def public_photos(item_id):
    item_id = sec.positive_id(item_id)
    with connect() as con:
        row = con.execute("SELECT image,image_public FROM items WHERE id=?", (item_id,)).fetchone()
        if not row or not row[1]:
            return []
        photos = [entry[0] for entry in con.execute(
            "SELECT image FROM item_photos WHERE item_id=? ORDER BY position", (item_id,))]
    return photos or ([row[0]] if row[0] else [])


def item_timeline(item_id, token):
    """등록자 또는 해당 수령자에게 답변 내용 없이 처리 시각만 제공합니다."""
    item_id = sec.positive_id(item_id)
    with connect() as con:
        viewer = require_session(con, token)
        item = con.execute("SELECT i.found_at,r.code_hash FROM items i JOIN registrations r ON r.item_id=i.id WHERE i.id=?",
                           (item_id,)).fetchone()
        if not item:
            raise sec.SecurityError("처리 이력을 확인할 수 없습니다.")
        owner = viewer == item[1]
        requests = con.execute("""SELECT id,created_at,status FROM pickup_requests WHERE item_id=?
            AND (? OR requester_hash=?) ORDER BY created_at,id""", (item_id, owner, viewer)).fetchall()
        if not owner and not requests:
            raise sec.SecurityError("수령 인증을 접수한 뒤 처리 이력을 확인할 수 있습니다.")
        entries = [{"stage": "물건 발견", "at": item[0], "detail": "등록된 발견 시각"}]
        entries.extend({"stage": "수령 인증 접수", "at": row[1], "detail": f"인증 #{row[0]}"} for row in requests)
        request_ids = {row[0] for row in requests}
        labels = {"create_items": "습득물 등록", "ownership_auto_approved": "소유 확인 완료",
                  "ownership_approved": "등록자 승인", "ownership_rejected": "인증 반려",
                  "pickup_completed": "반환 완료", "pickup_completed_by_recipient": "반환 완료"}
        # 이 물건의 공통 이벤트와 접근 가능한 수령 요청의 이벤트만 추립니다.
        events = con.execute("""SELECT created_at,action,subject_id FROM security_events
            WHERE (subject_id=? AND action IN ('create_items','pickup_completed','pickup_completed_by_recipient'))
            OR (action IN ('ownership_auto_approved','ownership_approved','ownership_rejected')
                AND subject_id IN (SELECT id FROM pickup_requests WHERE item_id=? AND (? OR requester_hash=?)))
            ORDER BY created_at,id""", (item_id, item_id, owner, viewer)).fetchall()
        for at, action, subject in events:
            detail = f"인증 #{subject}" if action.startswith("ownership_") and subject in request_ids else ""
            entries.append({"stage": labels[action], "at": at, "detail": detail})
        # 이전 버전 자료는 실제로 남아 있는 반환 기록만 보충합니다.
        returned = con.execute("SELECT created_at FROM pickups WHERE item_id=?", (item_id,)).fetchone()
        if returned and not any(entry["stage"] == "반환 완료" for entry in entries):
            entries.append({"stage": "반환 완료", "at": returned[0], "detail": "기존 반환 기록"})
    # 초 단위로 같은 시각이 기록되어도 등록 → 인증 → 반환 순서를 유지합니다.
    stage_order = {"물건 발견": 0, "습득물 등록": 1, "수령 인증 접수": 2,
                   "소유 확인 완료": 3, "등록자 승인": 3, "인증 반려": 4, "반환 완료": 5}
    return sorted(entries, key=lambda entry: (entry["at"], stage_order[entry["stage"]]))


def insert(table, values, owner_session=None, *, photo_paths=None):
    """등록·신고 입력을 검증하고 확인 코드로 연결된 사용자에게 기록을 귀속합니다."""
    allowed = {
        "items": {"title", "category", "color", "description", "place", "lat", "lon", "found_at", "storage", "image", "private_hint", "verification_question", "image_public", "manual_verification"},
        "reports": {"title", "category", "color", "keywords", "start_at", "end_at", "route", "radius", "photo_embedding"},
    }
    if table not in allowed or not set(values).issubset(allowed[table]):
        raise sec.SecurityError("허용되지 않는 필드입니다.")
    values = dict(values)
    if photo_paths is not None:
        if table != "items" or not 1 <= len(photo_paths) <= 3 or values.get("image") != photo_paths[0]:
            raise sec.SecurityError("등록할 사진을 확인해 주세요.")
        if len(set(photo_paths)) != len(photo_paths) or any(not sec.image_path(DATA, path) for path in photo_paths):
            raise sec.SecurityError("저장한 사진을 확인해 주세요.")
    for field in ("image_public", "manual_verification"):
        if field in values and (type(values[field]) not in (bool, int) or values[field] not in (0, 1)):
            raise sec.SecurityError("사진 공개 및 확인 방식을 확인해 주세요.")
    for name, maximum in {"title":80,"description":1000,"storage":250,"private_hint":500,"verification_question":200,"keywords":200}.items():
        if name in values:
            values[name] = sec.clean(values[name], name, maximum, 1 if name in {"title","description","storage"} else 0)
    if values.get("category") not in CATEGORIES or values.get("color") not in COLORS:
        raise sec.SecurityError("종류와 색상을 확인해 주세요.")
    if table == "items":
        place = values.get("place")
        p = places()
        if place not in ["없음"] + list(p.index):
            raise sec.SecurityError("발견 장소를 확인해 주세요.")
        values["lat"] = None if place == "없음" else float(p.loc[place, "lat"])
        values["lon"] = None if place == "없음" else float(p.loc[place, "lon"])
        if pd.Timestamp(values.get("found_at")) > pd.Timestamp(now()) or pd.isna(pd.Timestamp(values.get("found_at"))):
            raise sec.SecurityError("발견 시각을 확인해 주세요.")
        if values.get("image") and not sec.image_path(DATA, values["image"]):
            raise sec.SecurityError("저장한 사진을 확인해 주세요.")
    else:
        if values.get("photo_embedding") is not None:
            from photo_match import unpack_embedding
            unpack_embedding(values["photo_embedding"])
        try:
            route = __import__("json").loads(values["route"])
            if not isinstance(route, list) or len(route) > 6 or any(n not in places().index for n in route):
                raise ValueError()
            start, end = pd.Timestamp(values["start_at"]), pd.Timestamp(values["end_at"])
            if pd.isna(start) or pd.isna(end) or start > end or end > pd.Timestamp(now()) or values["radius"] not in (50,100,200):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise sec.SecurityError("분실 시간과 경로를 확인해 주세요.") from None
    with connect() as con:
        owner_hash = require_session(con, owner_session)
        sec.limited(con, now(), "create:" + owner_hash, limit=20, seconds=3600)
        keys = ",".join(values)
        marks = ",".join("?" for _ in values)
        item_id = con.execute(f"INSERT INTO {table} ({keys}) VALUES ({marks})", list(values.values())).lastrowid
        if table == "items":
            con.execute("INSERT INTO registrations (item_id,code_hash) VALUES (?,?)", (item_id, owner_hash))
            if photo_paths:
                con.executemany("INSERT INTO item_photos(item_id,position,image) VALUES (?,?,?)",
                                [(item_id, index, path) for index, path in enumerate(photo_paths)])
        else:
            con.execute("INSERT INTO report_owners (report_id,owner_hash) VALUES (?,?)", (item_id, owner_hash))
            con.execute("INSERT INTO report_views VALUES (?,(SELECT COALESCE(MAX(id),0) FROM items))", (item_id,))
        sec.event(con, now(), "create_" + table, item_id)
        return item_id


def code_hash(code):
    return sec.digest(sec.normalize_code(code))


def require_session(con, token):
    """현재 세션 토큰을 검사해 접근 가능한 사용자 식별값을 반환합니다."""
    if not isinstance(token, str) or not 20 <= len(token) <= 128:
        raise sec.SecurityError("확인 코드로 다시 연결해 주세요.")
    row = con.execute("SELECT owner_hash FROM owner_sessions WHERE token_hash=? AND expires_at>?",
                      (sec.digest(token), now().isoformat())).fetchone()
    if not row:
        raise sec.SecurityError("연결이 만료됐습니다. 확인 코드로 다시 연결해 주세요.")
    return row[0]


def issue_session(con, owner_hash):
    token = secrets.token_urlsafe(32)
    con.execute("DELETE FROM owner_sessions WHERE expires_at<=?", (now().isoformat(),))
    con.execute("INSERT INTO owner_sessions VALUES (?,?,?)", (sec.digest(token), owner_hash, (now() + timedelta(hours=1)).isoformat()))
    return token


def create_owner(actor):
    with connect() as con:
        sec.limited(con, now(), "new-owner:" + str(actor), limit=10, seconds=3600)
        sec.limited(con, now(), "new-owner-global", limit=100, seconds=3600)
        code = "PNU-DEMO" if not con.execute("SELECT 1 FROM owners LIMIT 1").fetchone() else sec.new_owner_code()
        hashed = code_hash(code)
        while con.execute("SELECT 1 FROM owners WHERE code_hash=? UNION SELECT 1 FROM owner_aliases WHERE alias_hash=?", (hashed,hashed)).fetchone():
            code = sec.new_owner_code()
            hashed = code_hash(code)
        con.execute("INSERT INTO owners VALUES (?,?)", (hashed, now().isoformat()))
        token = issue_session(con, hashed)
        sec.event(con, now(), "owner_created")
    return code, token


def authenticate_owner(code, actor):
    """확인 코드로 기존 내역에 연결하고 반복 입력 횟수를 제한합니다."""
    hashed = code_hash(code)
    # Attempt counters are committed independently of the authentication result.
    with connect() as con:
        sec.limited(con, now(), "auth-global", 100, 60)
        sec.limited(con, now(), "auth-actor:" + str(actor), 10, 300)
        sec.limited(con, now(), "auth-target:" + hashed, 10, 300)
    with connect() as con:
        allowed = con.execute("SELECT owner_hash FROM owner_aliases WHERE alias_hash=?", (hashed,)).fetchone()
        if not allowed:
            allowed = con.execute("SELECT code_hash FROM owners WHERE code_hash=?", (hashed,)).fetchone()
        if allowed:
            token = issue_session(con, allowed[0])
        sec.event(con, now(), "owner_login", outcome="ok" if allowed else "denied")
    if not allowed:
        raise sec.SecurityError("코드를 확인할 수 없습니다. 이전 버전 등록물은 로컬 복구 도구를 이용해 주세요.")
    return token


def logout(token):
    with connect() as con:
        con.execute("DELETE FROM owner_sessions WHERE token_hash=?", (sec.digest(token),))
        sec.event(con, now(), "logout")


def owner_items(token):
    with connect() as con:
        owner_hash = require_session(con, token)
        return pd.read_sql_query("""SELECT i.*, p.created_at AS picked_up_at, p.note AS pickup_note, p.seen_at
            FROM items i JOIN registrations r ON r.item_id=i.id
            LEFT JOIN pickups p ON p.item_id=i.id WHERE r.code_hash=? ORDER BY i.id DESC""",
            con, params=[owner_hash])


def owner_reports(token):
    with connect() as con:
        owner_hash = require_session(con, token)
        return pd.read_sql_query("SELECT p.* FROM reports p JOIN report_owners r ON r.report_id=p.id WHERE r.owner_hash=? ORDER BY p.id DESC",
                                 con, params=[owner_hash])


def update_item_privacy(item_id, token, image_public, manual_verification):
    """본인 등록물의 공개 범위와 승인 방식을 변경합니다."""
    if type(image_public) is not bool or type(manual_verification) is not bool:
        raise sec.SecurityError("공개 및 승인 설정을 확인해 주세요.")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        owner_hash = require_session(con, token)
        changed = con.execute("""UPDATE items SET image_public=?,manual_verification=?
            WHERE id=? AND EXISTS (SELECT 1 FROM registrations r WHERE r.item_id=items.id AND r.code_hash=?)""",
            (image_public, manual_verification, sec.positive_id(item_id), owner_hash)).rowcount
        if not changed:
            raise sec.SecurityError("본인이 등록한 물건만 설정을 변경할 수 있습니다.")
        if manual_verification:
            con.execute("""UPDATE pickup_requests SET status='확인 대기',
                verification_reason='등록자 최종 승인이 필요합니다.'
                WHERE item_id=? AND status='소유 확인 완료' AND verification_engine='local-minilm-onnx'""", (item_id,))
        sec.event(con, now(), "item_privacy_updated", item_id)


def submit_feedback(actor, message):
    """이용 의견은 공개 목록과 분리해 저장합니다."""
    message = sec.clean(message, "이용 의견", 1000, 1)
    actor = sec.clean(actor, "연결 정보", 128, 1)
    with connect() as con:
        sec.limited(con, now(), "feedback:" + sec.digest(actor), 3, 3600)
        return con.execute("INSERT INTO feedback(message,created_at) VALUES (?,?)",
                           (message, now().isoformat())).lastrowid


def delete_report(report_id, token):
    """현재 사용자가 작성한 분실 신고만 삭제합니다."""
    report_id = sec.positive_id(report_id)
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        owner_hash = require_session(con, token)
        if not con.execute("SELECT 1 FROM report_owners WHERE report_id=? AND owner_hash=?",
                           (report_id, owner_hash)).fetchone():
            raise sec.SecurityError("본인이 등록한 분실 신고만 삭제할 수 있습니다. 이미 삭제된 신고인지 확인해 주세요.")
        con.execute("DELETE FROM report_owners WHERE report_id=? AND owner_hash=?", (report_id, owner_hash))
        con.execute("DELETE FROM reports WHERE id=?", (report_id,))
        sec.event(con, now(), "delete_report", report_id)


def favorite_ids(token):
    """관심 목록은 현재 확인 코드 소유자에게만 조회합니다."""
    with connect() as con:
        owner = require_session(con, token)
        return {row[0] for row in con.execute("SELECT item_id FROM favorites WHERE owner_hash=?", (owner,))}


def set_favorite(item_id, token, saved):
    if type(saved) is not bool:
        raise sec.SecurityError("저장 여부를 확인해 주세요.")
    item_id = sec.positive_id(item_id)
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        owner = require_session(con, token)
        if not con.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
            raise sec.SecurityError("물건을 찾을 수 없습니다.")
        if saved:
            existing = con.execute("SELECT 1 FROM favorites WHERE owner_hash=? AND item_id=?", (owner,item_id)).fetchone()
            count = con.execute("SELECT COUNT(*) FROM favorites WHERE owner_hash=?", (owner,)).fetchone()[0]
            if not existing and count >= 100:
                raise sec.SecurityError("관심 물건은 최대 100개까지 저장할 수 있습니다.")
            con.execute("INSERT OR IGNORE INTO favorites VALUES (?,?,?)", (owner,item_id,now().isoformat()))
        else:
            con.execute("DELETE FROM favorites WHERE owner_hash=? AND item_id=?", (owner,item_id))


def report_updates(token):
    """마지막 조회 이후 등록된 기본 조건 후보 수만 계산하며 AI 판정으로 표현하지 않습니다."""
    with connect() as con:
        owner = require_session(con, token)
        return pd.read_sql_query("""SELECT r.id,r.title,COUNT(i.id) AS new_count FROM reports r
            JOIN report_owners o ON o.report_id=r.id JOIN report_views v ON v.report_id=r.id
            JOIN items i ON i.id>v.last_item_id AND i.category=r.category
                AND i.found_at>=r.start_at AND i.status!='반환 완료'
            WHERE o.owner_hash=? GROUP BY r.id,r.title ORDER BY r.id DESC""", con, params=[owner])


def mark_report_viewed(report_id, token, seen_item_id=None):
    with connect() as con:
        owner = require_session(con, token)
        if not con.execute("SELECT 1 FROM report_owners WHERE report_id=? AND owner_hash=?", (report_id,owner)).fetchone():
            raise sec.SecurityError("본인의 분실 신고만 확인할 수 있습니다.")
        latest = con.execute("SELECT COALESCE(MAX(id),0) FROM items").fetchone()[0]
        cursor = latest if seen_item_id is None else min(max(0,int(seen_item_id)),latest)
        con.execute("UPDATE report_views SET last_item_id=MAX(last_item_id,?) WHERE report_id=?", (cursor,report_id))


def mark_collected(item_id, note=""):
    raise sec.SecurityError("등록자 승인과 일회용 수령번호 확인이 필요합니다.")


def acknowledge_pickup(item_id, token):
    with connect() as con:
        owner_hash = require_session(con, token)
        return con.execute("""UPDATE pickups SET seen_at=? WHERE item_id=? AND seen_at IS NULL
            AND EXISTS (SELECT 1 FROM registrations r WHERE r.item_id=pickups.item_id AND r.code_hash=?)""",
            (now().isoformat(), item_id, owner_hash)).rowcount > 0


def _pickup_item(con,item_id,requester):
    row=con.execute("SELECT i.status,r.code_hash,i.private_hint,i.verification_question,i.manual_verification FROM items i JOIN registrations r ON r.item_id=i.id WHERE i.id=?",(item_id,)).fetchone()
    if not row or row[0]=="반환 완료":
        raise sec.SecurityError("수령 인증을 접수할 수 없습니다. 등록자 연결과 처리 상태를 확인해 주세요.")
    if row[1]==requester:
        raise sec.SecurityError("등록자와 수령자는 서로 다른 확인 코드로 연결해야 합니다.")
    return row


def _active_pickup(con,item_id,requester):
    return con.execute("""SELECT id,status,otp_hash FROM pickup_requests WHERE item_id=? AND requester_hash=?
        AND status IN ('확인 대기','소유 확인 완료') AND expires_at>? ORDER BY id DESC LIMIT 1""",
        (item_id,requester,now().isoformat())).fetchone()


def request_pickup(item_id, token, evidence, note=""):
    """비공개 답변으로 소유를 확인하고 수령 요청과 일회용 번호를 발급합니다."""
    item_id = sec.positive_id(item_id)
    evidence = sec.clean(evidence, "확인 질문의 답변", 1000, 1)
    note = sec.clean(note, "메시지", 500)
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        requester = require_session(con, token)
        row = _pickup_item(con,item_id,requester)
        existing = _active_pickup(con,item_id,requester)
        if existing and existing[1] == "소유 확인 완료":
            raise sec.SecurityError("이미 본인 확인이 완료됐습니다. 내 수령 인증에서 보관 장소를 확인해 주세요.")
        # Reserve attempts in a short committed transaction, before slow inference.
        sec.limited(con, now(), "request:" + requester, 10, 3600)
        sec.limited(con, now(), f"answer:{item_id}:{requester}", 5, 3600)

    # No database connection or write lock remains held while the model runs.
    judgement={"passed":False,"score":None,"engine":"manual","reason":"등록자가 확인할 질문·정답을 설정하지 않았습니다."}
    if row[3] and row[2]:
        try:judgement=judge_answer(row[2],evidence,row[3])
        except ModelUnavailable:
            judgement.update(engine="unavailable",reason="로컬 AI 모델을 불러오지 못했습니다. 모델 파일과 라이브러리를 확인해 주세요.")
    if row[4] and judgement["passed"]:
        judgement.update(passed=False, reason="AI 답변 비교를 통과했습니다. 등록자의 최종 확인을 기다려 주세요.")
    otp = f"{secrets.randbelow(100_000_000):08d}"
    salt = secrets.token_hex(16)
    hashed_otp=sec.otp_digest(otp,salt)
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        if require_session(con,token)!=requester or _pickup_item(con,item_id,requester)!=row:
            raise sec.SecurityError("물건 정보나 연결이 변경되었습니다. 다시 인증해 주세요.")
        if _active_pickup(con,item_id,requester)!=existing:
            raise sec.SecurityError("다른 요청에서 인증 상태가 변경되었습니다. 최신 수령 내역을 확인해 주세요.")
        status = "소유 확인 완료" if judgement["passed"] else "확인 대기"
        if existing:
            rid = existing[0]
            con.execute("""UPDATE pickup_requests SET evidence=?,note=?,otp_hash=?,otp_salt=?,expires_at=?,status=? WHERE id=?""",
                (evidence, note, hashed_otp, salt, (now() + timedelta(hours=1)).isoformat(), status, rid))
        else:
            rid = con.execute("""INSERT INTO pickup_requests
                (item_id,requester_hash,evidence,note,otp_hash,otp_salt,expires_at,created_at,status) VALUES (?,?,?,?,?,?,?,?,?)""",
                (item_id, requester, evidence, note, hashed_otp, salt,
                 (now() + timedelta(hours=1)).isoformat(), now().isoformat(), status)).lastrowid
        sec.event(con, now(), "pickup_requested", item_id)
        if status == "소유 확인 완료":
            sec.event(con, now(), "ownership_auto_approved", rid)
        con.execute("UPDATE pickup_requests SET verification_score=?,verification_engine=?,verification_reason=? WHERE id=?",
                    (judgement["score"],judgement["engine"],judgement["reason"],rid))
    return rid, otp


def pickup_requests(token, received=True):
    """사용자의 수령 요청을 조회하고 인증 상태에 따라 보관 장소를 공개합니다."""
    with connect() as con:
        owner_hash = require_session(con, token)
        condition = "r.code_hash=?" if received else "p.requester_hash=?"
        return pd.read_sql_query(f"""SELECT p.id,p.item_id,p.evidence,p.note,p.expires_at,p.created_at,p.status,i.title,
            p.verification_score,p.verification_engine,p.verification_reason,
            CASE WHEN p.status='반환 완료' OR (p.status='소유 확인 완료' AND p.expires_at>?) THEN i.storage ELSE NULL END AS storage
            FROM pickup_requests p JOIN items i ON i.id=p.item_id JOIN registrations r ON r.item_id=p.item_id
            WHERE {condition} ORDER BY p.id DESC""", con, params=[now().isoformat(), owner_hash])


def approve_ownership(request_id, token):
    """등록자가 유효한 수령 요청의 소유 확인을 직접 승인합니다."""
    with connect() as con:
        owner_hash = require_session(con, token)
        changed = con.execute("""UPDATE pickup_requests SET status='소유 확인 완료',
            verification_engine='manual-approved'
            WHERE id=? AND status='확인 대기' AND expires_at>?
            AND EXISTS (SELECT 1 FROM registrations r JOIN items i ON i.id=r.item_id
                WHERE r.item_id=pickup_requests.item_id AND r.code_hash=? AND i.status<>'반환 완료')""",
            (sec.positive_id(request_id), now().isoformat(), owner_hash)).rowcount
        if not changed:
            raise sec.SecurityError("권한 또는 인증 유효시간을 확인해 주세요.")
        sec.event(con, now(), "ownership_approved", request_id)


def renew_pickup_otp(request_id, token):
    """Only the verified claimant may replace a live pickup code."""
    request_id=sec.positive_id(request_id)
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        requester=require_session(con,token)
        row=con.execute("""SELECT p.otp_hash,p.otp_salt FROM pickup_requests p JOIN items i ON i.id=p.item_id
            WHERE p.id=? AND p.requester_hash=? AND p.status='소유 확인 완료' AND p.expires_at>?
            AND i.status<>'반환 완료'""",(request_id,requester,now().isoformat())).fetchone()
        if not row:
            raise sec.SecurityError("수령자 연결과 인증 유효시간을 확인해 주세요. 만료됐다면 특징을 다시 인증해 주세요.")
        sec.limited(con,now(),f"renew-otp:{request_id}",3,3600)
        otp=f"{secrets.randbelow(100_000_000):08d}"
        while sec.same(sec.otp_digest(otp,row[1]),row[0]):
            otp=f"{secrets.randbelow(100_000_000):08d}"
        salt=secrets.token_hex(16)
        con.execute("UPDATE pickup_requests SET otp_hash=?,otp_salt=? WHERE id=?",
                    (sec.otp_digest(otp,salt),salt,request_id))
        sec.event(con,now(),"pickup_otp_renewed",request_id)
    return otp


def complete_pickup(request_id, token, otp, handoff=False, *, as_recipient=False):
    """일회용 번호와 권한을 확인한 뒤 요청과 물건을 함께 반환 완료로 기록합니다."""
    request_id = sec.positive_id(request_id)
    if handoff is not True or not isinstance(otp, str) or not __import__("re").fullmatch(r"[0-9]{8}", otp.strip()):
        raise sec.SecurityError("실제 전달을 확인하고 8자리 수령 인증번호를 입력해 주세요.")
    with connect() as con:
        owner_hash = require_session(con, token)
        scope = "p.requester_hash=?" if as_recipient is True else "r.code_hash=?"
        row = con.execute(f"""SELECT p.item_id,p.otp_hash,p.otp_salt,p.note FROM pickup_requests p
            JOIN registrations r ON r.item_id=p.item_id JOIN items i ON i.id=p.item_id
            WHERE p.id=? AND {scope} AND p.status='소유 확인 완료' AND p.expires_at>? AND i.status<>'반환 완료'""",
            (request_id, owner_hash, now().isoformat())).fetchone()
        if not row:
            raise sec.SecurityError("권한 또는 인증 유효시간을 확인해 주세요.")
        sec.limited(con, now(), f"otp:{request_id}:{row[1]}", 5, 3600)
    correct = sec.same(sec.otp_digest(otp.strip(), row[2]), row[1])
    with connect() as con:
        sec.event(con, now(), "pickup_otp", request_id, "ok" if correct else "denied")
    if not correct:
        raise sec.SecurityError("수령 인증번호를 확인해 주세요. 5회 실패했다면 수령자가 ‘수령번호 다시 받기’를 눌러 주세요.")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        # Recheck the session, expiry, status, and owner inside the final transaction.
        owner_hash = require_session(con, token)
        scope = "requester_hash=?" if as_recipient is True else "EXISTS (SELECT 1 FROM registrations r WHERE r.item_id=pickup_requests.item_id AND r.code_hash=?)"
        changed = con.execute(f"""UPDATE pickup_requests SET status='반환 완료' WHERE id=? AND status='소유 확인 완료'
            AND expires_at>? AND {scope} AND otp_hash=?""",
            (request_id, now().isoformat(), owner_hash, row[1])).rowcount
        if not changed or not con.execute("UPDATE items SET status='반환 완료' WHERE id=? AND status<>'반환 완료'", (row[0],)).rowcount:
            raise sec.SecurityError("이미 처리됐거나 인증이 만료됐습니다.")
        con.execute("INSERT INTO pickups(item_id,note,created_at) VALUES (?,?,?)", (row[0], row[3], now().isoformat()))
        con.execute("UPDATE pickup_requests SET status='종료' WHERE item_id=? AND id<>? AND status IN ('확인 대기','소유 확인 완료')", (row[0], request_id))
        con.execute("UPDATE claims SET status='종료' WHERE item_id=? AND status IN ('접수','확인 중')", (row[0],))
        sec.event(con, now(), "pickup_completed_by_recipient" if as_recipient is True else "pickup_completed", row[0])


def confirm_received(request_id, token, otp, received=False):
    """Authenticated, ownership-verified claimant acknowledges actual collection."""
    return complete_pickup(request_id, token, otp, handoff=received, as_recipient=True)


def reject_pickup(request_id, token):
    with connect() as con:
        owner_hash = require_session(con, token)
        changed = con.execute("""UPDATE pickup_requests SET status='반려' WHERE id=? AND status IN ('확인 대기','소유 확인 완료')
            AND EXISTS (SELECT 1 FROM registrations r WHERE r.item_id=pickup_requests.item_id AND r.code_hash=?)""",
            (sec.positive_id(request_id), owner_hash)).rowcount
        if not changed:
            raise sec.SecurityError("권한 또는 처리 상태를 확인해 주세요.")
        sec.event(con, now(), "ownership_rejected", request_id)
        sec.event(con, now(), "pickup_rejected", request_id)


def places():
    return pd.read_csv(BASE / "places.csv").set_index("name")


def route_coords(names):
    p = places()
    return [tuple(p.loc[n, ["lat", "lon"]].astype(float)) for n in names if n in p.index]


def route_distance(point, route):
    """발견 지점에서 이동 경로의 가장 가까운 선분까지의 거리를 구합니다."""
    """국소 평면 근사. 선분 내부와 양 끝점을 모두 고려하며 단위는 미터."""
    if not route:
        raise ValueError("경로가 비어 있습니다")
    lat0, lon0 = point
    def xy(p):
        return ((p[1] - lon0) * math.pi / 180 * 6371000 * math.cos(math.radians(lat0)),
                (p[0] - lat0) * math.pi / 180 * 6371000)
    points = [xy(p) for p in route]
    best = min(math.hypot(*p) for p in points)
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        dx, dy = bx - ax, by - ay
        norm = dx * dx + dy * dy
        t = max(0, min(1, -(ax * dx + ay * dy) / norm)) if norm else 0
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


@lru_cache(maxsize=2048)
def _public_feature_matches(feature, title, description):
    """공개 제목·설명의 단서를 문자열과 로컬 AI로 비교합니다."""
    phrases = [phrase.strip() for phrase in re.split(r"[,;|\n。]+|\.\s+", description)]
    phrases = [phrase for phrase in [title, *phrases] if phrase]
    key = compact(feature)
    if any(key in compact(phrase) and not contradicts(phrase, feature) for phrase in phrases):
        return True
    # Use the same local model as pickup verification on public text only.
    for start in range(0, min(len(phrases), 15), 5):
        try:
            if judge_answer("|".join(phrases[start:start + 5]), feature)["passed"]:
                return True
        except (ModelUnavailable, RuntimeError):
            return False
    return False


def search(items, category="전체", color="전체", keywords="", route=None,
           radius=100, start=None, end=None, strict=True):
    """공개 검색은 조건에 맞는 물건을 추리고, 분실 신고는 항목별 점수로 후보를 정렬합니다."""
    result = items.loc[items.status.ne("반환 완료")].copy()
    parts = ["category_score", "color_score", "keyword_score", "location_score"]
    for part in parts:
        result[part] = 0.0
    if category != "전체":
        result["category_score"] = result.category.eq(category) * 40.0
        if strict:
            result = result.loc[result.category.eq(category)].copy()
    if color != "전체":
        result["color_score"] = result.color.eq(color) * 20.0
        if strict:
            result = result.loc[result.color.eq(color)].copy()
    if route:
        distances = [route_distance((r.lat, r.lon), route) if pd.notna(r.lat) and pd.notna(r.lon)
                     else float("nan") for r in result.itertuples()]
        result["distance_m"] = pd.Series(distances, index=result.index, dtype=float)
        if strict:
            result = result.loc[result.distance_m.le(radius)].copy()
        # 분실 신고의 동선은 순위를 돕는 조건이며, 범위 밖/위치 미등록 후보도 남깁니다.
        result["location_score"] = (1 - result.distance_m / radius).clip(lower=0).fillna(0) * 20
    if start is not None:
        result = result.loc[pd.to_datetime(result.found_at).ge(pd.Timestamp(start))].copy()
    if end is not None:
        result = result.loc[pd.to_datetime(result.found_at).le(pd.Timestamp(end))].copy()
    # 조회 조건을 적용한 뒤 공개 특징을 비교합니다.
    if strict:
        tokens = list(dict.fromkeys(keywords.casefold().split()))
        for token in tokens:
            text = (result.title + " " + result.description + " " + result.color + " " + result.category).str.casefold()
            hit = text.str.contains(token, regex=False)
            result["keyword_score"] += hit * (20 / len(tokens))
            result = result.loc[hit].copy()
    else:
        features = {compact(part): part.strip() for part in keywords.split(",") if compact(part)}
        tokens = list(features.values())
        if tokens:
            result["keyword_score"] = [
                20 * sum(_public_feature_matches(feature, title, description) for feature in tokens) / len(tokens)
                for title, description in zip(result.title, result.description)
            ]
    result["score"] = result[parts].sum(axis=1)
    if not strict:
        result = result.loc[result.score.gt(0)].copy()
    result = result.sort_values(["score", "found_at"], ascending=False)
    result.attrs["score_max"] = (40 if category != "전체" else 0) + (20 if color != "전체" else 0) + (20 if tokens else 0) + (20 if route else 0)
    result.attrs["score_limits"] = dict(zip(parts, (40 if category != "전체" else 0,
        20 if color != "전체" else 0, 20 if tokens else 0, 20 if route else 0)))
    return result


def seed_demo():
    if os.getenv("CAMPUS_ALLOW_DEMO") != "1":
        raise sec.SecurityError("예시 데이터 생성은 로컬 개발 모드에서만 사용할 수 있습니다.")
    p = places()
    with connect() as con:
        if con.execute("SELECT COUNT(*) FROM items").fetchone()[0]:
            return False
        samples = [
            ("검정 반지갑", "지갑", "검정", "가죽 재질, 작은 은색 로고", "의생명과학도서관"),
            ("흰색 무선 이어폰", "전자기기", "흰색", "투명 케이스, 별 스티커", "간호대학"),
            ("파란 장우산", "우산", "파랑", "나무 손잡이, 긴 우산", "나래관"),
            ("회색 후드 집업", "의류", "회색", "앞주머니, 면 소재", "경암공학관"),
        ]
        for i, (title, category, color, desc, place) in enumerate(samples):
            con.execute("""INSERT INTO items
                (title,category,color,description,place,lat,lon,found_at,storage,private_hint)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (title, category, color, desc, place,
                float(p.loc[place, "lat"]), float(p.loc[place, "lon"]),
                (now() - timedelta(hours=i + 1)).isoformat(), "데모 보관함 (실제 보관처 아님)",
                "데모 확인 특징: 안쪽에 별 표시"))
            item_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.execute("INSERT INTO registrations (item_id,code_hash) VALUES (?,?)", (item_id, code_hash("PNU-DEMO")))
        con.execute("INSERT OR IGNORE INTO owners VALUES (?,?)", (code_hash("PNU-DEMO"), now().isoformat()))
    from demo_photos import install_demo_photos
    install_demo_photos()
    return True
