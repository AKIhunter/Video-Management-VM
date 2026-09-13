"""角色与权限：current_user / require_admin，以及标签接口的「增可、删限 admin」约束。

全部使用临时库与自造数据，不接触真实库；断言只涉及角色字符串 / 状态码 / 结构。
"""
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import authz, db  # noqa: E402
from app.routers import media  # noqa: E402


def _mk_con(tmp_path, monkeypatch, role="admin"):
    from app import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "load", lambda: {
        "roots": [], "category_filter": ["视频"],
        "db_path": str(tmp_path / "t.db"), "default_user_id": 1,
    })
    con = db.connect()
    db.init(con)
    con.execute("UPDATE users SET role=? WHERE id=1", (role,))
    con.commit()
    return con


# ---------------- current_user ----------------
def test_current_user_role_comes_from_db(tmp_path, monkeypatch):
    for role in ("admin", "user"):
        con = _mk_con(tmp_path, monkeypatch, role=role)
        u = authz.current_user(con=con)
        assert u["id"] == 1 and u["role"] == role
        con.close()


def test_current_user_404_when_missing(tmp_path, monkeypatch):
    con = _mk_con(tmp_path, monkeypatch)
    con.execute("DELETE FROM users")
    con.commit()
    with pytest.raises(HTTPException) as e:
        authz.current_user(con=con)
    assert e.value.status_code == 401
    con.close()


# ---------------- require_admin ----------------
def test_require_admin_gate():
    assert authz.require_admin({"id": 1, "role": "admin"})["role"] == "admin"
    with pytest.raises(HTTPException) as e:
        authz.require_admin({"id": 2, "role": "user"})
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e2:
        authz.require_admin({"id": 3})
    assert e2.value.status_code == 403


# ---------------- 标签接口的路由级权限约束 ----------------
def _dep_names(route) -> set:
    names, stack = set(), [route.dependant]
    while stack:
        d = stack.pop()
        if d.call is not None:
            names.add(getattr(d.call, "__name__", str(d.call)))
        stack.extend(d.dependencies)
    return names


def _route(method: str, path: str):
    for r in media.router.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
            return r
    raise AssertionError(f"未找到路由 {method} {path}")


def test_tags_add_allows_any_user_but_delete_requires_admin():
    """规则：任何账号可加标签；删除仅 admin（DELETE 挂 require_admin）。"""
    post = _dep_names(_route("POST", "/api/media/{mid}/tags"))
    delete = _dep_names(_route("DELETE", "/api/media/{mid}/tags"))
    assert "current_user" in post and "require_admin" not in post
    assert "require_admin" in delete
