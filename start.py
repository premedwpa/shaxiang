# -*- coding: utf-8 -*-
# qidong.py v5.1 —— 让云端 AI Agent 控制你的 Windows 电脑
# =====================================================================
# 任务类型：
#   ping / read_file / write_file / list_dir / file_get / file_put
#   shell / run_python / screenshot / keyboard / mouse / open / wait
#
# ⚠️ 首次运行会引导你填写 base_url、token 和 device，保存在本地 config.json 中。
# ⚠️ 运行本脚本 = 授权云端控制本机；Ctrl+C 或删除即收回权限。
# =====================================================================
__version__ = "5.1"

import base64
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from logging.handlers import RotatingFileHandler

# ------------------------------------------------------------ 控制台编码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ------------------------------------------------------------ 常量
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "qidong.log")

DEFAULT_CONFIG = {
    "base_url": "",
    "token": "",
    "device": "win-pc",
    "poll_interval": 2,
    "heartbeat_interval": 30,
    "max_file_size": 20 * 1024 * 1024,
    "work_dir": "",
    "allow_shell": False,
    "allow_python": False,
    "allow_file_write": False,
    "log_level": "INFO"
}

# 危险命令黑名单（基础防护，无法阻止 run_python 绕过）
BLOCKED = [
    "format ", "del /", "rd /s", "rmdir /s", "shutdown",
    "reg delete", "vssadmin", "bcdedit", ":(){:|:&};:",
    "rm -rf /", "mkfs", "dd if=", "> /dev/sda"
]

# 运行时全局
CFG = DEFAULT_CONFIG.copy()
GUI = {"ready": False}

# ------------------------------------------------------------ 日志
logger = logging.getLogger("qidong")
logger.setLevel(logging.INFO)
_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

try:
    _fh = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
    _fh.setFormatter(_formatter)
    logger.addHandler(_fh)
except Exception:
    pass

_ch = logging.StreamHandler()
_ch.setFormatter(_formatter)
logger.addHandler(_ch)


# ============================================================ 配置加载
def load_config():
    """加载配置：命令行 > 环境变量 > config.json > 交互式输入"""
    cfg = DEFAULT_CONFIG.copy()

    # 1. config.json
    cfg_path = CONFIG_FILE
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--config" and i + 1 < len(args):
            cfg_path = args[i + 1]

    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
            logger.info(f"已加载配置：{cfg_path}")
        except Exception as e:
            logger.warning(f"读取配置文件失败：{e}")

    # 2. 环境变量覆盖（同时兼容 QIDONG_* 和 EAR_* 前缀）
    if os.environ.get("EAR_BASE") or os.environ.get("QIDONG_BASE"):
        cfg["base_url"] = os.environ.get("EAR_BASE") or os.environ["QIDONG_BASE"]
    if os.environ.get("EAR_TOKEN") or os.environ.get("QIDONG_TOKEN"):
        cfg["token"] = os.environ.get("EAR_TOKEN") or os.environ["QIDONG_TOKEN"]
    if os.environ.get("EAR_DEVICE"):
        cfg["device"] = os.environ["EAR_DEVICE"]

    # 3. 命令行参数覆盖
    for i, arg in enumerate(args):
        if arg in ("--base", "-b") and i + 1 < len(args):
            cfg["base_url"] = args[i + 1]
        if arg in ("--token", "-t") and i + 1 < len(args):
            cfg["token"] = args[i + 1]
        if arg in ("--device", "-d") and i + 1 < len(args):
            cfg["device"] = args[i + 1]

    # 4. 交互式输入
    if not cfg["base_url"] or not cfg["token"]:
        print("=" * 62)
        print("  首次运行 · 请填写云端连接信息")
        print("  这些信息只保存在本地的 config.json，不会上传")
        print("=" * 62)
        if not cfg["base_url"]:
            cfg["base_url"] = input("云端地址 (base_url): ").strip().rstrip("/")
        if not cfg["token"]:
            cfg["token"] = input("设备 Token: ").strip()
        if not cfg.get("device"):
            cfg["device"] = input("设备名 (device，默认 win-pc): ").strip() or "win-pc"
        if cfg["base_url"] and cfg["token"]:
            try:
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
                print(f"✓ 已保存到 {cfg_path}")
            except Exception as e:
                print(f"✗ 保存失败：{e}")
        else:
            print("✗ base_url 或 token 为空，程序退出。")
            sys.exit(1)

    if not cfg.get("device"):
        cfg["device"] = "win-pc"

    return cfg


# ============================================================ 依赖
def _ensure_gui_deps():
    """首次使用 GUI 功能时安装 pyautogui 和 pyperclip"""
    if GUI["ready"]:
        return
    for pkg in ("pyautogui", "pyperclip"):
        try:
            __import__(pkg)
        except ImportError:
            logger.info(f"安装 {pkg}...")
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", pkg],
                           timeout=300, check=True)
    import pyautogui
    pyautogui.FAILSAFE = True
    GUI["ready"] = True


# ============================================================ HTTP
def api(path, payload=None, retries=3):
    """带重试的 HTTP 请求"""
    data = json.dumps(payload).encode() if payload is not None else None
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                CFG["base_url"] + path,
                data=data,
                method="POST" if payload is not None else "GET",
                headers={"X-Token": CFG["token"], "Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            last_exc = e
            logger.warning(f"API 失败 ({attempt}/{retries}) {path}：{e}")
            if attempt < retries:
                time.sleep(1.5 ** attempt)
    raise last_exc


def _res(text, image=""):
    if len(image) > 12_000_000:
        return {"ok": False, "text": "图片过大，已丢弃", "image": ""}
    return {"ok": True, "text": str(text)[:8000], "image": image[:12_000_000]}


def _run_process(args, timeout=60, shell=False):
    """执行子进程，智能解码，避免 UnicodeDecodeError"""
    try:
        proc = subprocess.run(args, shell=shell, capture_output=True, timeout=timeout)
        def decode(b):
            for enc in ("utf-8", "gbk"):
                try:
                    return b.decode(enc)
                except UnicodeDecodeError:
                    continue
            return b.decode("utf-8", errors="replace")
        return proc.returncode, decode(proc.stdout or b""), decode(proc.stderr or b"")
    except subprocess.TimeoutExpired:
        return -1, "", f"命令超时 ({timeout}s)"
    except Exception as e:
        return -1, "", f"执行异常：{e}"


# ============================================================ 安全确认
def _confirm(action, detail):
    """高风险操作前弹窗确认"""
    if CFG.get("_no_gui"):
        return True
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        result = messagebox.askyesno(
            "⚠️ 高危操作确认",
            f"类型：{action}\n\n{detail[:500]}\n\n是否允许执行？"
        )
        root.destroy()
        return result
    except Exception as e:
        logger.warning(f"弹窗失败，默认拒绝：{e}")
        return False


def _safe_path(path):
    """限制文件访问在 work_dir 内（如果配置了）"""
    work_dir = CFG.get("work_dir") or ""
    if not work_dir:
        return path
    abs_path = os.path.abspath(path)
    abs_work = os.path.abspath(work_dir)
    if not abs_path.startswith(abs_work):
        raise PermissionError(f"路径 {path} 不在允许的工作目录 {work_dir} 内")
    return abs_path


# ============================================================ 任务执行
def execute(t):
    typ = t.get("type")
    task_id = t.get("id", "?")
    logger.info(f"任务 {task_id} · {typ}")
    try:
        if typ == "ping":
            return _res(f"pong · 设备:{CFG.get('device')} "
                        f"· 主机:{os.environ.get('COMPUTERNAME', '?')} "
                        f"· 用户:{os.environ.get('USERNAME', '?')} · {sys.version.split()[0]}")

        if typ == "read_file":
            path = _safe_path(t["path"])
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return _res(f.read()[:8000])

        if typ == "write_file":
            if not CFG.get("allow_file_write"):
                return _res("⛔ 写文件功能未开启（allow_file_write=false）")
            path = _safe_path(t["path"])
            content = t.get("content", "")
            if not _confirm("写入文件", f"路径：{path}\n大小：{len(content)} 字符"):
                return _res("✗ 用户拒绝")
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return _res(f"已写入 {path} · {len(content)} 字符")

        if typ == "list_dir":
            path = t.get("path", ".")
            path = _safe_path(path)
            items = []
            for n in sorted(os.listdir(path)):
                p = os.path.join(path, n)
                tag = "DIR " if os.path.isdir(p) else f"FILE {os.path.getsize(p)}B"
                items.append(f"{tag} {n}")
            return _res("\n".join(items) or "(空目录)")

        if typ == "file_get":
            path = _safe_path(t["path"])
            size = os.path.getsize(path)
            if size > CFG["max_file_size"]:
                return _res(f"文件过大 {size}B")
            with open(path, "rb") as f:
                return _res(f"已上传 {path} · {size}B",
                            image="fb64:" + base64.b64encode(f.read()).decode())

        if typ == "file_put":
            if not CFG.get("allow_file_write"):
                return _res("⛔ 写文件功能未开启")
            path = _safe_path(t["path"])
            raw = base64.b64decode(t["content_b64"])
            if len(raw) > CFG["max_file_size"]:
                return _res(f"文件过大 {len(raw)}B")
            if not _confirm("下载文件到本机", f"路径：{path}\n大小：{len(raw)}B"):
                return _res("✗ 用户拒绝")
            with open(path, "wb") as f:
                f.write(raw)
            return _res(f"已下载 {path} · {len(raw)}B")

        if typ == "shell":
            if not CFG.get("allow_shell"):
                return _res("⛔ Shell 功能未开启（allow_shell=false）")
            cmd = t.get("cmd", "")
            if any(b in cmd.lower() for b in BLOCKED):
                return _res(f"⛔ 已拦截危险命令：{cmd}")
            if not _confirm("执行 Shell 命令", cmd):
                return _res("✗ 用户拒绝")
            code, out, err = _run_process(cmd, timeout=t.get("timeout", 60), shell=True)
            full = out + (f"\n[stderr] {err}" if err else "")
            return _res(f"[退出码 {code}]\n" + (full[:8000] or "(无输出)"))

        if typ == "run_python":
            if not CFG.get("allow_python"):
                return _res("⛔ Python 执行未开启（allow_python=false）")
            code_str = t.get("code", "")
            if not _confirm("执行 Python 代码", code_str):
                return _res("✗ 用户拒绝")
            code, out, err = _run_process([sys.executable, "-c", code_str], timeout=120)
            full = out + (f"\n[stderr] {err}" if err else "")
            return _res(full[:8000] or f"(无输出, 退出码 {code})")

        if typ == "screenshot":
            return _screenshot()

        if typ == "keyboard":
            return _keyboard(t)

        if typ == "mouse":
            return _mouse(t)

        if typ == "open":
            target = t["target"]
            if not _confirm("打开目标", target):
                return _res("✗ 用户拒绝")
            os.startfile(target)
            return _res(f"已打开 {target}")

        if typ == "wait":
            time.sleep(min(float(t.get("seconds", 1)), 60))
            return _res(f"已等待 {t.get('seconds')} 秒")

        return _res(f"未知任务类型：{typ}")
    except Exception as e:
        logger.exception(f"任务 {task_id} 异常")
        return {"ok": False, "text": f"执行异常：{e}", "image": ""}


# ============================================================ 截屏
def _screenshot():
    try:
        from PIL import ImageGrab
        import io
        buf = io.BytesIO()
        ImageGrab.grab().convert("RGB").save(buf, "JPEG", quality=55, optimize=True)
        img = "jb64:" + base64.b64encode(buf.getvalue()).decode()
        return _res(f"截图成功 {len(img)//1024}KB", image=img)
    except ImportError:
        ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
              "$b=New-Object Drawing.Bitmap([Windows.Forms.Screen]::PrimaryScreen.Bounds.Width,"
              "[Windows.Forms.Screen]::PrimaryScreen.Bounds.Height);"
              "$g=[Drawing.Graphics]::FromImage($b);$g.CopyFromScreen(0,0,0,0,$b.Size);"
              "$ms=New-Object IO.MemoryStream;"
              "$b.Save($ms,[Drawing.Imaging.ImageFormat]::Png);"
              "[Convert]::ToBase64String($ms.ToArray())")
        code, out, err = _run_process(["powershell", "-NoProfile", "-Command", ps], timeout=90)
        if code != 0 or not out.strip():
            raise RuntimeError(f"截屏失败：{err[:200]}")
        return _res(f"截图成功 {len(out)//1024}KB", image="b64:" + out.strip())


# ============================================================ 键盘
def _keyboard(t):
    _ensure_gui_deps()
    import pyautogui
    act = t.get("action", "type")
    if act == "type":
        text = t.get("text", "")
        if any(ord(c) > 127 for c in text):
            import pyperclip
            pyperclip.copy(text)
            time.sleep(0.1)
            pyautogui.hotkey("ctrl", "v")
        else:
            pyautogui.typewrite(text, interval=0.02)
        return _res(f"已输入：{text[:50]}")
    if act == "press":
        pyautogui.press(t.get("keys", "enter"))
        return _res(f"已按键 {t.get('keys')}")
    if act == "hotkey":
        keys = t.get("keys", ["ctrl", "s"])
        if isinstance(keys, list):
            pyautogui.hotkey(*keys)
        else:
            pyautogui.hotkey(*keys.split("+"))
        return _res(f"已按组合键 {keys}")
    return _res(f"未知键盘动作：{act}")


# ============================================================ 鼠标
def _mouse(t):
    _ensure_gui_deps()
    import pyautogui
    act = t.get("action", "click")
    x, y = t.get("x"), t.get("y")
    if act == "move":
        pyautogui.moveTo(x, y, duration=0.3)
        return _res(f"已移动到 ({x},{y})")
    if act == "click":
        pyautogui.click(x=x, y=y, clicks=t.get("clicks", 1), button=t.get("button", "left"))
        return _res(f"已点击 ({x},{y})")
    if act == "double":
        pyautogui.doubleClick(x=x, y=y)
        return _res(f"已双击 ({x},{y})")
    if act == "drag":
        pyautogui.dragTo(x, y, duration=0.5)
        return _res(f"已拖拽到 ({x},{y})")
    if act == "scroll":
        pyautogui.scroll(t.get("dy", -300))
        return _res(f"已滚动 {t.get('dy')}")
    if act == "position":
        return _res(f"鼠标位置：{pyautogui.position()}")
    return _res(f"未知鼠标动作：{act}")


# ============================================================ 主循环
def _heartbeat():
    try:
        api("/api/heartbeat", {
            "name": os.environ.get("COMPUTERNAME", "win-pc"),
            "user": os.environ.get("USERNAME", "?")
        })
        logger.info("♡ 心跳已注册")
    except Exception as e:
        logger.warning(f"心跳失败：{e}")


def main():
    global CFG
    CFG = load_config()
    if not CFG["base_url"] or not CFG["token"]:
        print("✗ 缺少 base_url 或 token，退出。")
        sys.exit(1)

    logger.info(f"🎀 小耳朵 v{__version__} · 地址 {CFG['base_url']} · 设备 {CFG['device']}")
    logger.info(f"安全设置：shell={CFG['allow_shell']} python={CFG['allow_python']} "
                f"write={CFG['allow_file_write']} work_dir={CFG.get('work_dir') or '（无限制）'}")

    _heartbeat()
    _last_hb = time.time()

    while True:
        try:
            # 定时心跳（每 heartbeat_interval 秒）
            hb_interval = CFG.get("heartbeat_interval", 30)
            if time.time() - _last_hb > hb_interval:
                _heartbeat()
                _last_hb = time.time()

            # 领取属于本设备的任务
            t = api(f"/api/task?device={CFG['device']}")
            if t.get("id"):
                res = execute(t)
                api(f"/api/result/{t['id']}", res)
                logger.info(f"✔ 任务 {t['id']} 完成")
        except KeyboardInterrupt:
            logger.info("收到 Ctrl+C，退出。")
            break
        except Exception as e:
            logger.error(f"主循环异常：{e}")
            time.sleep(5)
        time.sleep(CFG.get("poll_interval", 2))


if __name__ == "__main__":
    main()
