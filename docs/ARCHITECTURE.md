# 架构分析：ComfyUI 模型迁移管理系统

> 分析对象：`ComfyUI 模型迁移管理系统（Python 后端版）.md`
> 分析方法：以 deep module（深模块）视角审查接口、seam 与职责归属；结论落到本仓库的可运行实现。

## 1. 文档给出的架构

```
Astro（4 个 Tab）
   │  fetch /api/*
   ▼
FastAPI routers ── services/parser.py, mover.py, refcount.py
   │
   ▼
SQLite（workflows / models / workflow_models / migrations） + config.json
```

四条主流程：

| 流程 | 入口 | 关键动作 |
|---|---|---|
| 配置 | Tab 2 | 保存 5 个路径 |
| 导入 | 拖拽上传 / 扫描目录 | 解析 JSON → 把引用写入 models、workflow_models |
| 分析 | 工作流行内「迁移」 | 判断模型是否被多个工作流共享 |
| 迁移 | 确认弹窗 | 原子复制到目标根，源文件保留，记日志 |

整体分层是对的：HTTP 适配、领域服务、SQLite、静态前端各就其位。下面只讨论会直接影响正确性和可维护性的部分。

## 2. 以深模块视角审查

### 2.1 模型身份没有定义（最关键）

文档中所有表、树、迁移都围绕 `rel_path`，但没有说明它从哪来。现实是：ComfyUI 的 `widgets_values` 通常只存 `xxx.safetensors`（甚至不含目录），而示例树却按 `models/diffusion_models/...` 分组。这意味着导入时**必须**用磁盘索引把“引用”解析成“相对路径”，否则：

- 同一文件在 ComfyUI 与 SD 两个根下会出现两个身份；
- 树无法知道文件属于 `diffusion_models` 还是 `vae`；
- 迁移拼出的目标路径可能缺目录。

文档把这一步留白，`routers/upload.py` 直接假定 `rels` 已经是可拼路径的 `rel_path`。

### 2.2 业务逻辑重复散落在三个 router 中

`upload.py::_register_workflow`、`workflows.py::scan_directory`、`workflows.py::_workflow_with_tree` 各自实现了一遍“upsert model + 建关联 + 查树”。三者在一个事务边界、命名、去重策略上已经开始分叉（上传代码尝试 stat 文件补 size，扫描代码固定写 0）。这是典型的浅模块：router 接口很大，但真正的行为没有归属。

**目标**：把“导入一份工作流”收敛为一个深模块（`services/importer.py`），上传与扫描只负责拿文件、调它、包 HTTP。

### 2.3 `_tree` 依赖脆弱的路径假设

`_tree` 假设所有 `rel_path` 都以 `models/` 开头、用 `/` 分隔。一旦出现反斜杠、绝对路径或裸文件名，分组就会错位。分组规则应独立成纯函数，并集中处理规范化。

### 2.4 迁移路径没有安全边界

`Path(root) / rel_path` 直接拼接。若 `rel_path` 含 `..` 或盘符，目标文件可能写到目标根之外；若目标根位于来源根内部，复制还可能自我递归。文档只提到“自动建目录 + 原子复制”，没有校验。

### 2.5 统计会重复计数，且每次请求都扫盘

`_scan_models` 分别扫 ComfyUI 与 SD 根再相加；若两个根指向同一目录（或 SD 根在 ComfyUI 根内），同一文件会被计两次。每次 `GET /api/stats` 都全量 `rglob`，模型多时首屏会明显变慢。

### 2.6 导入过程的健壮性缺口

- 上传先落临时文件再解析，未配置 `workflow_json_dir` 时临时文件永久留在系统 temp；
- 未清理上传文件名（可用 `..\..\x.json` 之类覆盖任意路径）；
- 没有大小上限；
- 扫描对每个 JSON 单独开连接、单独提交，失败一个不影响其他，但也无法整体回滚；
- 同一文件通过“上传”与“扫描”两条路径进入时，去重键不一致。

### 2.7 前端与后端存在隐性耦合

- `renderTree` 生成 `data-model=""`，迁移时再去 `GET /api/workflows` 反查 `model_id`，多一次全量请求且依赖返回结构；
- `doMigrate` 触发的是 `tab:activated` 事件，而该事件只用于“通知面板刷新”，并不会真正切换到日志 Tab；
- `data-rel` 直接拼进 HTML，未转义；
- 文档的 analyze 路径写作 `/api/migrate/analyze`，与独立 `analyze` router 不一致。

### 2.8 时间语义不统一

`migrations.ts` 若用 SQLite `CURRENT_TIMESTAMP` 则是 UTC，而界面按本地时间展示；文档没有约定。本实现统一存本地时间字符串，避免“刚迁移却显示 8 小时前”。

### 2.9 缺少可测试的 seam

文档没有测试，且 parser / mover 被 router 直接依赖。只要把这两个模块做成“接受输入、返回结果”的纯接口，就能在不启动 HTTP 的情况下覆盖最易错的行为。

### 2.10 已迁移到目标的模型会被误报为缺失

原设计只扫描 ComfyUI / SD 两个源根目录，`found=0` 就显示“未找到”。真实数据里目标目录 `I:/models` 已经存在 21 个模型，其中 11 处工作流引用只存在于目标；这些引用会被错误地当作缺失，用户无法区分“真的缺文件”和“已经迁移过”。

**目标**：引入模型清单（源 + 目标并集）与 `in_target` 状态；工作流树区分“已迁移 / 已在目标 / 未迁移 / 未找到”；迁移前先检查目标是否已有同大小、同修改时间的文件并跳过复制。

### 2.11 反斜杠路径在存储层反复转义

Windows 用户粘贴反斜杠，JSON / SQL / 日志 / HTML 属性各自需要不同转义。本实现把路径在进入系统时统一成 `/`，UNC 前缀保留 `//server/share`；`pathlib` 直接接受正斜杠。详见 [ADR 0003](adr/0003-forward-slash-paths.md)。

## 3. 目标设计

### 3.1 模块与接口

| 模块 | 接口（调用方需要知道的全部） | 深度来源 |
|---|---|---|
| `services/parser.py` | `extract_model_refs(doc) -> list[str]`：接受已解析的 JSON 对象，返回去重后的模型引用 | 兼容 UI 格式 / API 格式 / `properties.models`，过滤 URL 与非模型字符串，规范化路径 |
| `services/catalog.py` | `ModelCatalog(roots)` + `entries()` / `get(rel)` / `resolve(ref) -> ResolvedModel` | 根目录下探、递归索引、精确路径/文件名解析、多根去重与优先级、未解析占位、mtime |
| `services/importer.py` | `register_workflow(conn, catalog, name, path, refs, target) -> int` | 事务内的 workflow upsert、model upsert、关联去重、目标兜底、重复导入幂等 |
| `services/inventory.py` | `build_inventory(comfy, sd, target) -> {summary, models}` | 源 + 目标并集、每文件位置/大小/迁移状态、根目录存在性 |
| `services/tree.py` | `group_models(rows) -> list[Folder]` | 路径规范化与目录分组 |
| `services/mover.py` | `target_path(root, rel_path)`、`copy_atomic(src, dst)` | 路径越界防护、目标 `models/` 下探、`mkdir`、临时文件 + `os.replace`、失败清理 |
| `services/paths.py` | `to_posix_path(value) -> str` | 反斜杠/重复斜杠/trailing slash/UNC 的统一规范化 |
| `db.py` / `config_store.py` | `get_conn()`、`get_config()` / `save_config()` | 连接生命周期、schema、WAL、旧路径归一化、原子写配置 |
| `routers/*` | 仅 HTTP 语义 | 参数校验、错误码、响应拼装 |
| `web/` | 5 个 Tab 的静态页面 | 展示与交互，不再承担 model_id 推导 |

### 3.2 数据模型

```sql
workflows(id, name, file_path UNIQUE, imported_at)
models(id, rel_path UNIQUE, size, found)
workflow_models(workflow_id, model_id, PRIMARY KEY(workflow_id, model_id))
migrations(id, rel_path, src_path, dst_path, status, message, ts)
```

`file_path` 唯一使导入幂等；`rel_path` 唯一使跨工作流共享关系成立；`found` 区分真实文件与未解析占位。

### 3.3 两条主流程

**导入**

```
JSON bytes → json.loads → extract_model_refs
          → ModelCatalog.refresh() → resolve 每个引用
          → 源里没有时用目标清单补齐真实 rel_path（标 found=0）
          → 单事务：workflow upsert、model upsert、关联写入
```

**迁移**

```
rel_path → ModelCatalog.resolve（ComfyUI / SD / 目标，优先源）
         → 用户勾选目标位置（comfyui / sd / target）
         → dest_catalog.get（是否已有、大小/mtime 是否一致）
         → target_path(dest_root, rel_path)（越界校验）
         → 相同则跳过；否则 copy_atomic（临时文件 + os.replace）
         → move=true 时复制成功后删除源文件
         → migrations 记一行（成功/失败）
```

默认复制，源文件永不删除；只有用户在弹窗里显式勾选“剪贴”并二次确认，才会在目标写入成功后删除源。已有同大小、同 mtime 的文件时跳过复制；勾选剪贴时，跳过复制后仍会删除源文件（目标已确认存在）。见 [ADR 0004](adr/0004-optional-cut.md)。

### 3.4 明确的取舍

- 单用户、本机运行：不做鉴权、不做多租户、不做任务队列。
- 同步请求：大文件复制会占用一个线程，但实现与排错成本最低；WebSocket 进度留作后续迭代。
- 统计实时扫盘：对本地几十 GB 的模型目录可接受；若目录达到数十万文件，再引入缓存/后台索引。
- 前端构建产物由 FastAPI 直接托管：生产只需一个进程；开发时用 Astro dev server 代理 `/api`。
- 默认复制，避免误删共享模型；剪贴是显式、二次确认的破坏性操作，删除失败会保留源文件并记失败日志。

## 4. 验收 seam

测试只跨这些接口，不绕过它们查内部状态：

1. `to_posix_path` —— 反斜杠、UNC、盘符根、重复/尾部斜杠；
2. `extract_model_refs` —— 真实 ComfyUI UI/API 两种 JSON 的引用提取；
3. `ModelCatalog.resolve` / `get` —— 裸文件名、子目录、`models/` 前缀、多根、未解析；
4. `build_inventory` —— 源 + 目标并集、状态、根目录存在性；
5. `mover` —— 内容一致、源文件保留、嵌套目录创建、`..` 越界拒绝、mtime 跳过；
6. HTTP API —— 配置 → 上传 → 工作流树 → 共享分析 → 目标位置勾选/复制/剪贴 → 日志/统计 的完整闭环。

## 5. 与文档的差异（有意为之）

| 文档 | 本实现 | 原因 |
|---|---|---|
| `/api/migrate/analyze` | `GET /api/analyze` | 分析是独立能力，不隶属于迁移 |
| 临时文件保存上传 | 未配置目录时存 `data/uploads/` | 不把用户文件丢进系统 temp |
| `_tree` 内联 | `services/tree.py` | 可独立测试，供上传与列表复用 |
| 前端反查 model_id | 树节点直接带 `id` | 少一次全量请求 |
| `tab:activated` 切换页 | `tab:show` 事件负责切换 | 语义分开 |
| SQLite 默认时间 | 显式本地时间 | 展示一致 |
| 只统计源目录 | 源 + 目标并集，工作流树带 `in_target` | 已迁移模型不应误报缺失 |
| 迁移只复制 | 默认复制，显式勾选剪贴才删除源 | 满足清理源目录的诉求，同时保留默认安全行为 |
