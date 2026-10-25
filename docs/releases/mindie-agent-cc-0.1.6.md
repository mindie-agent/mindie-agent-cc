# mindie-agent-cc 0.1.6

本版本固定 remote-dev `1fefcc38`。命令、Bash 与 TaskOutput 的大日志现在由核心 formatter 给出有界 UTF-8 头尾、省略说明和完整远端/本地日志引用；增量页保持原样，不越过未返回字节。失败预览会以 preview degraded 明示，同时保留真实 exit/结果。

自动更新/清单继续保留 `MCP_TOOL_TIMEOUT=660000` 与 Claude profile；无正式 release 或全局宿主改动。
