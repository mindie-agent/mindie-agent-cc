# mindie-agent-cc 0.1.23

[English](mindie-agent-cc-0.1.23.en.md)

- **Windows `SessionStart` 同步 Job 托管与冷启动生命周期修复（`session_start.py`、`bootstrap.py`、`bounded.py`）**：将 `session_start.py` 与 `bootstrap.py` 中的 `os.execve` 统一收敛至 `bounded.exec_or_wait`（Windows 下经由 Job Object + `CREATE_NO_WINDOW` 同步阻塞等待子进程退出），消除 Windows CRT `execve` 立即退出父进程导致的 `SessionStart` 提前返回与后台 `uv` 孤儿竞争。
- **WSL2 跨环境二进制隔离与传输异常精确分类（`paths.py`、`component_update.py`、`release_update.py`）**：新增 `which_host_binary` 拦截 WSL2 `/mnt/<drive>` 与 PE `MZ` 可执行文件对 Linux 宿主 `claude`/`uv`/`gh`/组件 CLI 的遮蔽；补齐 `ssl.SSLError`、`SSLCertVerificationError`、`http.client` 流中断及 DNS 错误的非上报传输分类，同时保持源码完整性校验与本地状态机错误可上报。
