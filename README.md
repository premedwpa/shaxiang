# Qidong Agent

让云端 AI Agent（如智谱清言）远程控制你的 Windows 电脑的本地执行器。

## ⚠️ 安全警告

**本工具具有对电脑的高权限操作能力。使用前请阅读以下内容：**

- 仅用于授权控制**你自己的**设备。
- 严禁用于未经授权访问他人电脑。
- 默认禁用 `shell`、`run_python`、写文件等高风险功能，需在 `config.json` 中显式开启。
- 高风险操作会弹出本地确认对话框，请仔细核对。
- **不要**将 `config.json` 分享给他人或提交到 Git 仓库。
- 本项目作者不对使用本工具造成的任何损失负责。

## 功能

- 系统探测：`ping`
- 文件操作：`read_file` `write_file` `list_dir` `file_get` `file_put`
- 命令执行：`shell` `run_python`（默认关闭）
- 屏幕操作：`screenshot` `keyboard` `mouse`
- 其他：`open` `wait`

## 安装

1. 安装 Python 3.8+。
2. 安装依赖：
   ```bash
   pip install -r requirements.txt
