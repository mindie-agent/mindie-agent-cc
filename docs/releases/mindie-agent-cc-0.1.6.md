# mindie-agent-cc 0.1.6

本版本固定 remote-dev `4c40cb5` 与 mindie-diagnostics 0.5.4（`5ae643e`）。diagnostics 在 reader-registry/游标协调之前以 `O_EXCL` 分配首个/空闲日志槽，修复旧实现约 101ms setup 即触发的间歇 CI 失败；完整窗口读取与压力语义不变，未做异步改造。

命令、Bash 与 TaskOutput 的大日志现在由核心 formatter 给出有界 UTF-8 头尾、省略说明和完整远端原始日志引用与明确标注的本地缓存切片；增量页保持原样，不越过未返回字节。失败预览会以 preview degraded 明示，同时保留真实 exit/结果。

自动更新/清单继续保留 `MCP_TOOL_TIMEOUT=660000` 与 Claude profile；无正式 release 或全局宿主改动。另包含一处有意的测试取证改动：launch-entry 回归 helper 现在断言实际记录的 stage/category，并在日志缺失时输出 stdout/stderr/路径。
