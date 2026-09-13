"""角色/权限模型。

v1 单用户免登录：默认把请求视为 admin（user_id=1, name='admin', role='admin'）。
未来上线登录/鉴权层时，仅需把 current_user 改为从会话/token 解析，并让普通用户
返回 role='user'，即可让 require_admin 自动对普通用户只读化——无需改业务路由。
"""
from fastapi import Depends, HTTPException

from . import config
from .db import get_db


def current_user(con=Depends(get_db)) -> dict:
    """解析当前请求人。v1：取配置里的 ``default_user_id`` 对应的用户记录。

    角色来自 ``users.role`` 表数据（而非写死），因此：
    - 默认库 seed 的 id=1 是 admin → 等价于原来的固定 admin；
    - 只要把该用户改成 ``role='user'``（或后续登录层解析出普通用户），
      ``require_admin`` 就会自动对普通用户只读化，业务路由不用改。

    TODO(未来登录)：改为从会话 cookie / Authorization 头解析，落库校验后返回该用户。
    """
    cfg = config.load()
    uid = cfg.get("default_user_id") or 1
    row = con.execute("SELECT id, name, role FROM users WHERE id=?", (uid,)).fetchone()
    if row is None:
        raise HTTPException(401, "当前用户不存在")
    return {"id": row["id"], "name": row["name"], "role": row["role"] or "user"}


def require_admin(user: dict = Depends(current_user)) -> dict:
    """管理员守卫：仅 ``role='admin'`` 可执行。

    用于只读/只增的对比场景，例如「标签」——普通用户可增不可删（删除接口挂本守卫）。
    """
    if user.get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user