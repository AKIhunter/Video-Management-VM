import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .db import get_db
from .routers import account, admin, media, playback, userdata

WEBUI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webui")
log = logging.getLogger("vm.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动期：配置日志 + 初始化数据库（建表/迁移/seed admin）。"""
    from . import config as cfg_mod
    from .logging_setup import setup_logging
    setup_logging(cfg_mod.load())
    log.info("服务启动中… python=%s", sys.executable)
    con = db.connect()
    try:
        db.init(con)
    finally:
        con.close()
    log.info("数据库初始化完成")
    # 评分均值的空闲结算线程：仅在无长任务时批量重算（写入路径已即时重算，这里兜底）
    from .services.ratings import start_settler
    start_settler()
    yield


app = FastAPI(title="视频管理器", version="0.1.0",
              description="轻量级视频索引与查询管理器", lifespan=lifespan)


@app.get("/api/poster/{mid}")
def poster(mid: int, con=Depends(get_db)):
    row = con.execute("SELECT poster_path FROM media WHERE id=?", (mid,)).fetchone()
    if row and row["poster_path"] and os.path.exists(row["poster_path"]):
        return FileResponse(row["poster_path"])
    # 兜底封面：本地无素材时返回默认占位图，避免页面破图
    fb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                      "webui", "assets", "fallback.svg")
    if os.path.exists(fb):
        return FileResponse(fb)
    raise HTTPException(404, "无海报")


app.include_router(account.router)
app.include_router(media.router)
app.include_router(userdata.router)
app.include_router(playback.router)
app.include_router(admin.router)

# 前端静态资源（放最后，/api 路由优先匹配）
if os.path.isdir(WEBUI):
    app.mount("/", StaticFiles(directory=WEBUI, html=True), name="webui")
