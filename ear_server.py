# ear_server.py v3.1 —— 双向桥服务端：按设备定向分发 + 结果归属校验 + 设备心跳
# =====================================================================
# 端点：
#   GET  /health                  免鉴权存活探测
#   GET  /api/devices             设备列表（仅 admin）
#   GET  /api/task?device=NAME    领取属于该设备的任务（target 命中或 "*"）
#   POST /api/task                下发任务 {"params": {...}, "target": "win-pc" | "*"}
#   POST /api/result/{tid}        回传结果（仅认领者或 admin）
#   GET  /api/result/{tid}        读取结果（仅认领者或 admin）
#   POST /api/heartbeat           设备心跳（客户端每 30s 一次）
# 鉴权：X-Token → bridge_data/registry.json 里的 admin 或某台设备。
#       admin 可派给任意设备；设备令牌只能派给自己，越权一律 403。
# 存储：pending.json / results.json / devices.json 的读改写都由跨进程文件锁保护
# =====================================================================
import contextlib
import json
import os
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from typing import Iterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

# --- 控制台编码兜底 ---
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "bridge_data")
os.makedirs(DATA, exist_ok=True)
TASKS_F = os.path.join(DATA, "pending.json")
RESULTS_F = os.path.join(DATA, "results.json")
DEVICES_F = os.path.join(DATA, "devices.json")
LOCK_F = os.path.join(DATA, "bridge.lock")
REGISTRY_F = os.environ.get("EAR_REGISTRY") or os.path.join(DATA, "registry.json")

RESULT_TTL = float(os.environ.get("EAR_RESULT_TTL", "1800"))
RESULT_MAX = int(os.environ.get("EAR_RESULT_MAX", "200"))
ONLINE_WINDOW = float(os.environ.get("EAR_ONLINE_WINDOW", "15"))


# ============================================================ 生命周期
@contextlib.asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    _ensure_registry()
    print(f"· 文件锁后端 {LOCK_BACKEND} · 结果保留 {RESULT_TTL:.0f}s / 上限 {RESULT_MAX} 条",
          flush=True)
    yield


app = FastAPI(title="ear-bridge v3.1", lifespan=_lifespan)


# ============================================================ 跨进程文件锁
_tlock = threading.Lock()

try:
    import msvcrt

    def _lock_fd(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_fd(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

    LOCK_BACKEND = "msvcrt.locking"
except ImportError:
    try:
        import fcntl

        def _lock_fd(fh) -> None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

        def _unlock_fd(fh) -> None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

        LOCK_BACKEND = "fcntl.flock"
    except ImportError:
        def _lock_fd(fh) -> None:
            return None

        def _unlock_fd(fh) -> None:
            return None

        LOCK_BACKEND = "threading.Lock(降级)"
        print("⚠ msvcrt / fcntl 都不可用，文件锁降级为进程内锁", flush=True)


@contextlib.contextmanager
def _guard() -> Iterator[None]:
    with _tlock, open(LOCK_F, "a+b") as fh:
        try:
            if fh.seek(0, os.SEEK_END) == 0:
                fh.write(b"\0")
                fh.flush()
        except OSError:
            pass
        _lock_fd(fh)
        try:
            yield
        finally:
            _unlock_fd(fh)


def _load(f, default):
    try:
        with open(f, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _save(f, obj):
    with open(f, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)


def _stamp(ts: float | None = None) -> str:
    return time.strftime("%m-%d %H:%M:%S", time.localtime(ts if ts is not None else time.time()))


# ============================================================ 设备注册表
_registry_cache: dict | None = None
_registry_mtime: float = 0.0


def _new_registry() -> dict:
    return {
        "admin": {"name": "admin", "token": secrets.token_hex(16)},
        "devices": {
            "win-pc": {"token": secrets.token_hex(16), "caps": ["*"]},
            "sandbox": {"token": secrets.token_hex(16), "caps": ["*"]},
        },
    }


def _env_var_for(dev: str) -> str:
    return "EAR_TOKEN_" + re.sub(r"[^0-9A-Za-z]", "_", dev).upper()


def _with_env_overrides(reg: dict) -> dict:
    tok = (os.environ.get("EAR_ADMIN_TOKEN") or "").strip()
    if tok:
        reg.setdefault("admin", {})["token"] = tok
        reg["admin"].setdefault("name", "admin")
    for dev, meta in (reg.get("devices") or {}).items():
        tok = (os.environ.get(_env_var_for(dev)) or "").strip()
        if tok:
            meta["token"] = tok
    return reg


def _print_registry(reg: dict) -> None:
    rows = [("admin", (reg.get("admin") or {}).get("token", ""))]
    rows += [(str(dev), (meta or {}).get("token", ""))
             for dev, meta in (reg.get("devices") or {}).items()]
    left = max([len(r[0]) for r in rows] + [len("role/device")]) + 2
    edge = "-" * (left + 34)
    print("\n+-" + edge + "-+", flush=True)
    print("| " + "role/device".ljust(left) + "| " + "X-Token".ljust(32) + " |", flush=True)
    print("+-" + edge + "-+", flush=True)
    for name, tok in rows:
        print("| " + name.ljust(left) + "| " + str(tok).ljust(32) + " |", flush=True)
    print("+-" + edge + "-+", flush=True)
    print(f"注册表: {REGISTRY_F}", flush=True)
    print("把对应 Token 配到该机器的 EAR_TOKEN / QIDONG_TOKEN；ear_ctl 用 admin 那一行。\n",
          flush=True)


def _ensure_registry() -> dict:
    global _registry_cache, _registry_mtime
    try:
        mtime = os.path.getmtime(REGISTRY_F)
    except OSError:
        mtime = 0.0
    if _registry_cache is not None and mtime == _registry_mtime:
        return _registry_cache
    created = False
    try:
        with open(REGISTRY_F, encoding="utf-8") as fh:
            reg = json.load(fh)
    except FileNotFoundError:
        reg, created = _new_registry(), True
    except Exception as e:
        print(f"⚠ 注册表解析失败（{e}），已重新生成", flush=True)
        reg, created = _new_registry(), True
    if created:
        with open(REGISTRY_F, "w", encoding="utf-8") as fh:
            json.dump(reg, fh, ensure_ascii=False, indent=2)
    reg = _with_env_overrides(reg)
    if created:
        _registry_cache, _registry_mtime = reg, -1.0
        _print_registry(reg)
    else:
        _registry_cache, _registry_mtime = reg, mtime
    return reg


# ============================================================ 鉴权
@dataclass(frozen=True)
class Principal:
    name: str
    role: str  # "admin" | "device"


def check(x_token: str = Header(...)) -> Principal:
    reg = _ensure_registry()
    admin_tok = (reg.get("admin") or {}).get("token", "")
    if admin_tok and secrets.compare_digest(x_token, admin_tok):
        return Principal(name="admin", role="admin")
    for dev_name, meta in (reg.get("devices") or {}).items():
        tok = (meta or {}).get("token", "")
        if tok and secrets.compare_digest(x_token, tok):
            return Principal(name=dev_name, role="device")
    raise HTTPException(status_code=401, detail="无效的 X-Token")


# ============================================================ 模型
class TaskReq(BaseModel):
    params: dict
    target: str = "*"


# ============================================================ 端点
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/devices")
def list_devices(p: Principal = Depends(check)):
    if p.role != "admin":
        raise HTTPException(403, "仅 admin 可查看设备列表")
    with _guard():
        devices = _load(DEVICES_F, {})
    now = time.time()
    out = {}
    for name, info in devices.items():
        out[name] = {
            "online": (now - info.get("last_seen", 0)) <= ONLINE_WINDOW,
            "last_seen": _stamp(info.get("last_seen", 0)),
            "name": info.get("name", name),
            "user": info.get("user", "?"),
        }
    return out


@app.get("/api/task")
def fetch_task(device: str = Query(...), p: Principal = Depends(check)):
    """设备领取属于它的任务。设备令牌只能领自己的。"""
    if p.role != "admin" and p.name != device:
        raise HTTPException(403, "设备令牌只能领取自己的任务")
    with _guard():
        tasks = _load(TASKS_F, [])
        now = time.time()
        for t in tasks:
            if t.get("claimed_by"):
                continue
            target = t.get("target", "*")
            if target == device or target == "*":
                t["claimed_by"] = p.name
                t["claimed_at"] = now
                _save(TASKS_F, tasks)
                return t
    return {}


@app.post("/api/task")
def create_task(req: TaskReq, p: Principal = Depends(check)):
    """下发任务。设备令牌只能给自己派任务。"""
    if p.role != "admin" and req.target != p.name:
        raise HTTPException(403, "设备令牌只能给自己派任务")
    tid = secrets.token_hex(8)
    task = {
        "id": tid,
        "target": req.target,
        "claimed_by": None,
        "created_at": time.time(),
        **req.params,
    }
    with _guard():
        tasks = _load(TASKS_F, [])
        tasks.append(task)
        # 清理已认领或过期的任务，避免无限增长
        tasks = [t for t in tasks
                 if not t.get("claimed_by") or (time.time() - t.get("claimed_at", 0)) < 600]
        _save(TASKS_F, tasks)
    return {"id": tid, "ok": True}


@app.post("/api/result/{tid}")
def post_result(tid: str, payload: dict, p: Principal = Depends(check)):
    """回传结果。仅认领者或 admin。"""
    with _guard():
        tasks = _load(TASKS_F, [])
        owner = None
        for t in tasks:
            if t.get("id") == tid:
                owner = t.get("claimed_by") or t.get("target")
                break
        if p.role != "admin" and owner and owner != p.name:
            raise HTTPException(403, "不能替其他设备回传结果")

        results = _load(RESULTS_F, {})
        results[tid] = {
            "ok": bool(payload.get("ok", True)),
            "text": str(payload.get("text", ""))[:8000],
            "image": str(payload.get("image", ""))[:12_000_000],
            "device": p.name,
            "ts": time.time(),
        }
        # 清理过期结果
        cutoff = time.time() - RESULT_TTL
        items = [(k, v) for k, v in results.items() if v.get("ts", 0) >= cutoff]
        if len(items) > RESULT_MAX:
            items = sorted(items, key=lambda x: x[1].get("ts", 0), reverse=True)[:RESULT_MAX]
        _save(RESULTS_F, dict(items))

        # 从 pending 中移除已完成的任务
        tasks = [t for t in tasks if t.get("id") != tid]
        _save(TASKS_F, tasks)
    return {"ok": True}


@app.get("/api/result/{tid}")
def get_result(tid: str, p: Principal = Depends(check)):
    with _guard():
        results = _load(RESULTS_F, {})
    if tid not in results:
        return {}
    entry = results[tid]
    if p.role != "admin" and entry.get("device") != p.name:
        raise HTTPException(403, "不能读取其他设备的结果")
    return entry


@app.post("/api/heartbeat")
def heartbeat(payload: dict, p: Principal = Depends(check)):
    with _guard():
        devices = _load(DEVICES_F, {})
        devices[p.name] = {
            "last_seen": time.time(),
            "name": payload.get("name", p.name),
            "user": payload.get("user", "?"),
            "role": p.role,
        }
        _save(DEVICES_F, devices)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
