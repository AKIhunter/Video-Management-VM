"""角色/权限模型。

v1 单用户免登录：默认把请求视为 admin（user_id=1, name='admin', role='admin'）。
未来上线登录/鉴权层时，仅需把 current_user 改为从会话/token 解析，并让普通用户
返回 role='user'，即可让 require_admin 自动对普通用户只读化——无需改业务路由。
"""
from fastapi import Depends, HTTPException

from . import db
from .db import get_db


def current_user(con=Depends(get_db)) -> dict:
    """解析当前请求人。v1：固定返回默认 admin。

    TODO(未来登录)：改为从会话 cookie / Authorization 头解析，落库校验后返回该用户。
    """
    return {"id": 1, "name": "admin", "role": "admin"}


def require_admin(user: dict = Depends(current_user)) -> dict:
    """白屏管理入口守卫：仅 admin 可运维。普通用户抛 403。"""
    if user.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user