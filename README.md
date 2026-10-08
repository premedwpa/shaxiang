# Qidong Agent

让云端 AI Agent（如智谱清言）远程控制你的 Windows 电脑的本地执行器。
沙箱ai运行ear_server.py
本地运行start.py

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
具体步骤
1. 在服务端机器上启动 ear_server.py
bash
pip install fastapi uvicorn
python ear_server.py
启动后终端会打印一张令牌表，记下 win-pc 那一行的 Token，例如：

text
+-------------------------------------------------+
| role/device  | X-Token                          |
+-------------------------------------------------+
| admin        | dc8120f73935918a834e05cb054947fd |
| win-pc       | 06528388d8d6bd494d0c55f85c10a95d |
| sandbox      | 70521028d08976d18f30eddd9d5fd06f |
+-------------------------------------------------+
2. 把服务端暴露到公网
在服务端机器上运行：

bash
bash getcf.sh
./cf.bin tunnel --url http://127.0.0.1:9000
会得到一个地址，例如：

text
https://xxx.trycloudflare.com
这就是你的云端地址。

3. 在你的 Windows 电脑上启动 qidong.py
先安装依赖：

powershell
pip install -r requirements.txt
然后运行：

powershell
python qidong.py
首次运行会提示你输入：

text
云端地址 (base_url): https://xxx.trycloudflare.com
认证 Token: 06528388d8d6bd494d0c55f85c10a95d
填完后会自动保存到 config.json。看到下面这行就表示上线成功：

text
♡ 心跳已注册
直接在网站给agent说任务就能自动在你电脑上执行
