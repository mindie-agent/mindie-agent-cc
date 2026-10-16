# 框架稳定性与测试契约

<b>中文</b> | <a href="framework-stability.md">English</a> · <a href="README.md">返回文档索引</a>

本文说明 `mindie-agent-cc` 的日常无感使用保障、跨平台原子配置读写机制，以及可复现的确定性测试契约。

---

## 1. 日常使用与配置持久化保障

- **一次配置，持久生效**：用户的社区贡献开关、批准的项目路径范围以及独立的产品故障上报偏好在保存后，跨会话、`--fork-session` 子会话、宿主重启、插件升级与普通临时故障持久有效。日常研发任务无需每次手动激活或续期。
- **关闭共享时零额外开销**：当社区贡献处于关闭状态时，`Stop` Hook 不会读取会话记录，也不会调用整理模型；与此同时，本地知识检索（`mindie-knowledge`）与远端昇腾开发（`remote-dev`）工具依然随时按需可用。
- **损坏配置绝不静默重置**：短暂的服务或网络波动由后台 Worker 依托持久化状态自动恢复；若配置文件本身损坏，系统会直接作为显式故障报告，绝不会当成“首次安装”重新弹窗覆盖用户原有的选择。

---

## 2. Windows 平台原子配置读写

- **删除共享读取 + 原生原子重命名**：共享配置存储结合了删除共享（Delete-Sharing）读取与 Windows 原生原子重命名语义（参见 [`FILE_RENAME_INFORMATION`](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information)）。并发读取进程要么看到完整的旧配置文档，要么看到完整的新配置文档，绝不会读到写了一半的残缺 JSON。
- **外部占用安全保护**：在 Windows 上，单纯调用 Python 的 `os.replace` 无法替换正被打开的文件；通过读写两侧协同即可避免内部并发读写互相阻塞。若被未开启删除共享的外部程序强行锁死，更新操作会保留原配置并明确报错，不会触发死循环重试或配置丢失。

---

## 3. 可复现的组件与契约测试

仓库内的单元与集成测试均使用提交在仓库中的真实解析器夹具（Fixtures）以及 [`uv.lock`](../uv.lock) 中锁定的精确组件提交，**绝不探测或修改用户本机的生产环境安装**。

### 运行完整测试套件
在仓库根目录下，准备好与 `uv.lock` 中锁定提交完全一致的 `mindie-knowledge` 本地检出目录后执行：

```sh
uv sync --locked --no-editable --group test
MINDIE_TEST_KIMI_SCRIPTS="$PWD/tests/fixtures/kimi-86de2c3/scripts" \
MINDIE_TEST_KNOWLEDGE_CHECKOUT=/absolute/path/to/mindie-knowledge-pin \
uv run --locked --no-editable --group test python -m pytest -q
```

### 本地候选源身份校验说明
- `MINDIE_TEST_KNOWLEDGE_CHECKOUT` 必须指向一个本地 Git 检出目录的绝对路径，且其 `HEAD` 提交必须与 `uv.lock` 中声明的 `mindie-knowledge` 提交完全一致。
- 测试仅通过本地 `git` 命令核对该提交（不联网访问 GitHub，也不自动搜索相邻目录），随后在独立临时虚拟环境中复用已锁定的依赖包执行一次离线 `pip install --no-deps --no-build-isolation`，以验证 `direct_url.json` 溯源回执。

---

## 4. 集成与配套发布边界

- **组件先行发布顺序**：当共享运行时组件发生变更时，先发布底层共享组件（`mindie-knowledge`、`remote-dev`、`mindie-diagnostics`）的正式 Release，再更新并验证各原生宿主插件（`mindie-agent-cc`、`mindie-agent-codex`、`mindie-agent-kimi`）。
- **明确区分单测与实机验收**：自动化 CI 负责验证锁定依赖、解析器夹具与跨平台文件系统契约；原生宿主会话闭环与远端昇腾 NPU 实机验收作为独立证据记录，不以单测通过代替实机验收。
