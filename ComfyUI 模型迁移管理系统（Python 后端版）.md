# ComfyUI 模型迁移管理系统 - 四 Tab 完整实现

基于你提供的需求，我将重新组织为 **4 个 Tab 页面**，并增强以下能力：

- **多路径配置**：ComfyUI 模型路径、SD 模型路径、迁移目标路径、工作流 JSON 目录
- **拖拽上传 + 自动分析**：拖入 JSON 即解析并展示引用模型树
- **工作流树形展开**：按 📂 目录分组展示模型
- **日志操作**：查询、筛选、删除记录

> 后端沿用上版 Python/FastAPI 架构，主要扩展 **API** 与 **前端页面**。

---

## 一、新增/变更功能总览

| Tab | 功能 |
|---|---|
| **主页面** | 统计卡片：`.safetensors` 总数、工作流 JSON 数、已迁移数、存储占用；最近迁移/最近工作流速览 |
| **路径配置** | 5 个路径配置（ComfyUI 模型、SD 模型、迁移目标、工作流 JSON 目录、SD 工作流目录）；**拖拽上传区**（上传即解析并入库）；解析预览树 |
| **工作流页面** | 全部工作流列表（含磁盘扫描 + 自定义上传）；每行**点击展开** → 📂 树形模型；行内"迁移"按钮 |
| **日志记录** | 迁移记录表格（时间/文件/源/目标/状态）；按状态筛选；删除/清空操作 |

---

## 二、后端扩展

### 2.1 `schemas.py`（新增）

```python
from pydantic import BaseModel
from typing import Optional


class PathConfig(BaseModel):
    comfyui_models_root: str = ""    # D:\working\ComfyUI\models 或 D:\working\ComfyUI
    sd_models_root: str = ""          # D:\working\SD\models
    migrate_target_root: str = ""     # E:\working\ComfyUI
    workflow_json_dir: str = ""       # D:\working\ComfyUI\user\default\workflows
    sd_workflow_dir: str = ""         # D:\working\SD\workflows


class DashboardStats(BaseModel):
    total_models: int
    total_workflows: int
    migrated_count: int
    failed_count: int
    model_bytes: int
    recent_migrations: list[dict]
    recent_workflows: list[dict]


class UploadedFile(BaseModel):
    filename: str
    rel_paths: list[str]
    size: int
```

### 2.2 `routers/stats.py`（新增主页面统计）

```python
from fastapi import APIRouter
from pathlib import Path
from db import get_conn
from config_store import get as get_cfg

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _scan_models(root: str) -> tuple[int, int]:
    """递归统计 .safetensors 数量与总大小（仅统计 models 下）"""
    if not root:
        return 0, 0
    base = Path(root)
    # 若用户填的是 ComfyUI 根目录，则下探 models；否则直接扫
    if (base / "models").is_dir():
        base = base / "models"
    if not base.is_dir():
        return 0, 0
    count, total = 0, 0
    for p in base.rglob("*.safetensors"):
        count += 1
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return count, total


@router.get("")
def stats():
    cfg = get_cfg()
    with get_conn() as conn:
        wf_count = conn.execute("SELECT COUNT(*) c FROM workflows").fetchone()["c"]
        mig_ok = conn.execute(
            "SELECT COUNT(*) c FROM migrations WHERE status='success'").fetchone()["c"]
        mig_fail = conn.execute(
            "SELECT COUNT(*) c FROM migrations WHERE status='failed'").fetchone()["c"]

        recent_migs = [dict(r) for r in conn.execute("""
            SELECT rel_path, status, ts FROM migrations ORDER BY id DESC LIMIT 8
        """).fetchall()]

        recent_wfs = [dict(r) for r in conn.execute("""
            SELECT w.id, w.name, w.imported_at,
                   (SELECT COUNT(*) FROM workflow_models WHERE workflow_id=w.id) AS model_count
            FROM workflows w ORDER BY w.id DESC LIMIT 8
        """).fetchall()]

    # 合并 ComfyUI + SD 模型目录统计
    c1, s1 = _scan_models(cfg.comfyui_models_root)
    c2, s2 = _scan_models(cfg.sd_models_root)

    return {
        "total_models": c1 + c2,
        "total_workflows": wf_count,
        "migrated_count": mig_ok,
        "failed_count": mig_fail,
        "model_bytes": s1 + s2,
        "recent_migrations": recent_migs,
        "recent_workflows": recent_wfs,
    }
```

### 2.3 `routers/upload.py`（拖拽上传 + 解析）

上传的工作流 JSON 会**保存到 `workflow_json_dir`（配置则保存到临时目录）**，然后解析入库。

```python
import json
import tempfile
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from db import get_conn
from config_store import get as get_cfg
from services.parser import parse_workflow_file

router = APIRouter(prefix="/api/upload", tags=["upload"])


def _register_workflow(name: str, saved_path: Path, rels: list[str]) -> dict:
    cfg = get_cfg()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO workflows (name, file_path) VALUES (?, ?)",
            (name, str(saved_path))
        )
        wf_id = cur.lastrowid

        for rel in rels:
            row = conn.execute(
                "SELECT id FROM models WHERE rel_path=?", (rel,)).fetchone()
            if row:
                mid = row["id"]
            else:
                size = 0
                for root in (cfg.comfyui_models_root, cfg.sd_models_root):
                    if not root:
                        continue
                    try:
                        size = (Path(root) / rel).stat().st_size
                        if size:
                            break
                    except OSError:
                        pass
                cur2 = conn.execute(
                    "INSERT INTO models (rel_path, size) VALUES (?, ?)", (rel, size)
                )
                mid = cur2.lastrowid
            conn.execute(
                "INSERT OR IGNORE INTO workflow_models (workflow_id, model_id) VALUES (?,?)",
                (wf_id, mid)
            )

        rows = conn.execute("""
            SELECT m.id, m.rel_path, m.size FROM models m
            JOIN workflow_models wm ON wm.model_id = m.id
            WHERE wm.workflow_id = ? ORDER BY m.rel_path
        """, (wf_id,)).fetchall()

    return {
        "id": wf_id,
        "name": name,
        "file_path": str(saved_path),
        "models": [dict(r) for r in rows],
    }


@router.post("")
async def upload_workflow(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".json"):
        raise HTTPException(400, "仅支持 .json 工作流文件")

    cfg = get_cfg()
    content = await file.read()

    # 临时写入解析（先解析）
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        rels = parse_workflow_file(str(tmp_path))
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(400, f"解析失败: {e}")

    # 保存到 workflow_json_dir（未配置则保留在临时目录并提示）
    if cfg.workflow_json_dir:
        dest_dir = Path(cfg.workflow_json_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        save_path = dest_dir / file.filename
    else:
        save_path = tmp_path
    if save_path != tmp_path:
        save_path.write_bytes(content)
        tmp_path.unlink(missing_ok=True)

    wf = _register_workflow(file.filename, save_path, rels)
    return {"workflow": wf, "model_count": len(rels)}
```

### 2.4 `routers/workflows.py`（扩展：磁盘扫描 + 树形）

```python
from pathlib import Path
from fastapi import APIRouter, HTTPException
from db import get_conn
from config_store import get as get_cfg
from services.parser import parse_workflow_file

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


def _tree(models: list[dict]) -> list[dict]:
    """把 rel_path 分组为 📂 目录树
    输入: [{"rel_path": "models/diffusion_models/x.safetensors"}, ...]
    输出: [{"folder": "diffusion_models",
            "files": [{"name":"x.safetensors","rel_path":"models/diffusion_models/x.safetensors","size":123}]}]
    """
    groups: dict[str, list[dict]] = {}
    for m in models:
        rp = m["rel_path"]
        parts = rp.split("/")
        # 去掉头部 "models"
        idx = 1 if parts and parts[0] == "models" else 0
        folder = "/".join(parts[idx:-1]) or "(root)"
        groups.setdefault(folder, []).append({
            "name": parts[-1],
            "rel_path": rp,
            "size": m.get("size", 0),
        })
    return [
        {"folder": f, "files": sorted(v, key=lambda x: x["name"])}
        for f, v in sorted(groups.items())
    ]


def _workflow_with_tree(conn, wf) -> dict:
    models = conn.execute("""
        SELECT m.id, m.rel_path, m.size FROM models m
        JOIN workflow_models wm ON wm.model_id = m.id
        WHERE wm.workflow_id = ? ORDER BY m.rel_path
    """, (wf["id"],)).fetchall()
    item = dict(wf)
    item["models"] = [dict(m) for m in models]
    item["tree"] = _tree(item["models"])
    return item


@router.get("")
def list_workflows():
    with get_conn() as conn:
        wfs = conn.execute(
            "SELECT id, name, file_path, imported_at FROM workflows ORDER BY id DESC"
        ).fetchall()
        return [_workflow_with_tree(conn, wf) for wf in wfs]


@router.delete("/{wf_id}")
def delete_workflow(wf_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM workflows WHERE id=?", (wf_id,))
    return {"ok": True}


@router.post("/scan")
def scan_directory():
    """扫描 workflow_json_dir + sd_workflow_dir 下所有 .json 并入库（去重按 file_path）"""
    cfg = get_cfg()
    dirs = [d for d in (cfg.workflow_json_dir, cfg.sd_workflow_dir) if d]
    if not dirs:
        raise HTTPException(400, "请先在路径配置中设置工作流 JSON 目录")

    imported, skipped = [], 0
    for d in dirs:
        base = Path(d)
        if not base.is_dir():
            continue
        for jf in base.rglob("*.json"):
            with get_conn() as conn:
                exists = conn.execute(
                    "SELECT id FROM workflows WHERE file_path=?", (str(jf),)
                ).fetchone()
            if exists:
                skipped += 1
                continue
            try:
                rels = parse_workflow_file(str(jf))
            except Exception:
                continue
            with get_conn() as conn:
                cur = conn.execute(
                    "INSERT INTO workflows (name, file_path) VALUES (?, ?)",
                    (jf.name, str(jf))
                )
                wf_id = cur.lastrowid
                for rel in rels:
                    row = conn.execute(
                        "SELECT id FROM models WHERE rel_path=?", (rel,)).fetchone()
                    if row:
                        mid = row["id"]
                    else:
                        cur2 = conn.execute(
                            "INSERT INTO models (rel_path, size) VALUES (?, 0)", (rel,)
                        )
                        mid = cur2.lastrowid
                    conn.execute(
                        "INSERT OR IGNORE INTO workflow_models VALUES (?,?)",
                        (wf_id, mid)
                    )
            imported.append(jf.name)

    return {"imported": imported, "imported_count": len(imported), "skipped": skipped}
```

### 2.5 `routers/logs.py`（迁移日志）

```python
from fastapi import APIRouter
from db import get_conn

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
def list_logs(status: str = "", limit: int = 200):
    sql = "SELECT * FROM migrations"
    params = []
    if status in ("success", "failed", "pending"):
        sql += " WHERE status=?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


@router.delete("/{log_id}")
def delete_log(log_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM migrations WHERE id=?", (log_id,))
    return {"ok": True}


@router.delete("")
def clear_logs(status: str = ""):
    with get_conn() as conn:
        if status in ("success", "failed", "pending"):
            conn.execute("DELETE FROM migrations WHERE status=?", (status,))
        else:
            conn.execute("DELETE FROM migrations")
    return {"ok": True}
```

### 2.6 `main.py`（注册新路由）

```python
from routers import config, workflows, analyze, migrate, stats, upload, logs

app.include_router(config.router)
app.include_router(stats.router)
app.include_router(workflows.router)
app.include_router(upload.router)
app.include_router(analyze.router)
app.include_router(migrate.router)
app.include_router(logs.router)
```

> `config_store.PathConfig`、`services/mover.py`、`services/refcount.py`、`services/parser.py` 沿用上版，仅 `PathConfig` 字段扩为 5 个。

### 2.7 `services/mover.py` 小调整

原 `migrate_model(rel_path, source_root, target_root)` 保持不变；`migrate` 路由里按 `rel_path` 命中 ComfyUI/SD 哪边存在就取哪边的源：

```python
# routers/migrate.py
from pathlib import Path

@router.post("")
def execute_migration(req: MigrateReq):
    cfg = get_cfg()
    if not cfg.migrate_target_root:
        raise HTTPException(400, "请先配置迁移目标路径")

    results = []
    for rp in req.rel_paths:
        # 优先 ComfyUI，其次 SD
        src_root = None
        for root in (cfg.comfyui_models_root, cfg.sd_models_root):
            if root and (Path(root) / rp).is_file():
                src_root = root
                break
        if not src_root:
            results.append({
                "rel_path": rp, "src_path": "", "dst_path": "",
                "status": "failed", "message": "源文件未找到"
            })
            continue
        res = migrate_model(rp, src_root, cfg.migrate_target_root)
        results.append(res.model_dump())
    return {"results": results}
```

---

## 三、前端 Astro 改造

### 3.1 目录结构

```
src/
├── layouts/Layout.astro          # 含 Tab 导航
├── components/
│   ├── Tabs.astro
│   ├── tab/Dashboard.astro       # Tab 1
│   ├── tab/Paths.astro           # Tab 2
│   ├── tab/Workflows.astro       # Tab 3
│   └── tab/Logs.astro            # Tab 4
└── pages/index.astro
```

### 3.2 `src/layouts/Layout.astro`

```astro
---
const { title = "ComfyUI 模型迁移" } = Astro.props;
---
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width" />
  <title>{title}</title>
  <style>
    :root { --brand:#2b7cff; --border:#e6e8ef; --muted:#7a8093; }
    * { box-sizing: border-box; }
    body { font-family: system-ui,-apple-system,"Segoe UI",sans-serif;
           margin:0; background:#f5f6f9; color:#1f2330; }
    .container { max-width: 1180px; margin: 0 auto; padding: 20px 20px 60px; }
    .card { background:#fff; border-radius:12px; padding:18px 22px; margin-bottom:16px;
            box-shadow:0 1px 4px rgba(0,0,0,.06); }
    h1 { font-size:20px; margin:0 0 4px; }
    h2 { font-size:15px; margin:0 0 12px; color:#333; }
    .muted { color: var(--muted); }
    .mono { font-family: ui-monospace, Consolas, monospace; font-size:12px; }
    button { cursor:pointer; border:none; border-radius:8px; padding:8px 14px;
             background:var(--brand); color:#fff; font-size:13px; }
    button.ghost { background:#eef2fb; color:var(--brand); }
    button.danger { background:#e5484d; }
    button.small { padding:5px 10px; font-size:12px; }
    button:disabled { opacity:.55; cursor:not-allowed; }
    input[type=text] { width:100%; padding:8px 11px; border:1px solid var(--border);
                       border-radius:8px; font-size:13px; }
    label { font-size:12px; color:#556; display:block; margin:0 0 5px; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th,td { text-align:left; padding:9px 10px; border-bottom:1px solid #f0f1f6; }
    th { color:#556; font-weight:500; background:#fafbff; }
    .hidden { display:none !important; }
    .badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; }
    .badge.ok { background:#e6f7ed; color:#1a9a4b; }
    .badge.fail { background:#fdeaea; color:#d33; }
    .badge.pending { background:#f0f0f5; color:#666; }
    .grid { display:grid; gap:14px; }
    .grid.cols-4 { grid-template-columns: repeat(4, 1fr); }
    .grid.cols-2 { grid-template-columns: repeat(2, 1fr); }
    @media (max-width: 820px) { .grid.cols-4 { grid-template-columns: repeat(2,1fr); } }
    /* 树 */
    .tree { font-size:13px; line-height:1.7; }
    .tree .folder { color:#3b4a66; }
    .tree .file { color:#556; margin-left:18px; }
    /* 拖拽区 */
    .dropzone { border:2px dashed #cdd5e6; border-radius:12px; padding:28px;
                text-align:center; color:#889; cursor:pointer;
                transition:.15s; background:#fbfcff; }
    .dropzone.over { border-color:var(--brand); background:#f0f6ff; color:var(--brand); }
    /* Tab 导航 */
    .tabs { display:flex; gap:6px; border-bottom:1px solid var(--border); margin:16px 0 20px; }
    .tab { padding:10px 18px; cursor:pointer; font-size:14px; color:#556;
           border-bottom:2px solid transparent; user-select:none; }
    .tab.active { color:var(--brand); border-bottom-color:var(--brand); font-weight:600; }
    /* 树形展开行 */
    .wf-row { cursor:pointer; }
    .wf-row:hover { background:#fafbff; }
    .wf-tree { padding: 6px 18px 14px; background:#fafbff; }
    .caret { display:inline-block; width:14px; transition:.15s; color:#889; }
    .wf-expanded .caret { transform: rotate(90deg); }
  </style>
</head>
<body>
  <div class="container"><slot /></div>
</body>
</html>
```

### 3.3 `src/components/Tabs.astro`

```astro
---
const tabs = [
  { id: 'dashboard', label: '📊 主页面' },
  { id: 'paths',     label: '⚙️ 路径配置' },
  { id: 'workflows', label: '🗂️ 工作流' },
  { id: 'logs',      label: '📝 日志记录' },
];
---
<nav class="tabs" id="tabNav">
  {tabs.map((t, i) => (
    <div class:list={['tab', { active: i === 0 }]} data-tab={t.id}>{t.label}</div>
  ))}
</nav>

<script>
  const nav = document.getElementById('tabNav')!;
  const activate = (id: string) => {
    nav.querySelectorAll<HTMLElement>('.tab').forEach(el => {
      el.classList.toggle('active', el.dataset.tab === id);
    });
    document.querySelectorAll<HTMLElement>('.tab-panel').forEach(p => {
      p.classList.toggle('hidden', p.dataset.panel !== id);
    });
    // 通知各面板刷新数据
    document.dispatchEvent(new CustomEvent('tab:activated', { detail: { id } }));
  };
  nav.querySelectorAll<HTMLElement>('.tab').forEach(el => {
    el.addEventListener('click', () => activate(el.dataset.tab!));
  });
  activate('dashboard');
</script>
```

### 3.4 Tab 1 — `components/tab/Dashboard.astro`

```astro
---
---
<section class="tab-panel" data-panel="dashboard">
  <div class="grid cols-4" id="statsCards">
    <div class="card"><div class="muted">模型文件（.safetensors）</div><div style="font-size:26px;font-weight:600" id="stModels">–</div></div>
    <div class="card"><div class="muted">工作流 JSON</div><div style="font-size:26px;font-weight:600" id="stWorkflows">–</div></div>
    <div class="card"><div class="muted">已成功迁移</div><div style="font-size:26px;font-weight:600;color:#1a9a4b" id="stMigrated">–</div></div>
    <div class="card"><div class="muted">迁移失败</div><div style="font-size:26px;font-weight:600;color:#d33" id="stFailed">–</div></div>
  </div>

  <div class="grid cols-2">
    <div class="card">
      <h2>最近迁移</h2>
      <table><thead><tr><th>文件</th><th>状态</th><th>时间</th></tr></thead>
        <tbody id="recentMig"><tr><td colspan="3" class="muted">加载中…</td></tr></tbody></table>
    </div>
    <div class="card">
      <h2>最近工作流</h2>
      <table><thead><tr><th>名称</th><th>模型数</th><th>导入</th></tr></thead>
        <tbody id="recentWf"><tr><td colspan="3" class="muted">加载中…</td></tr></tbody></table>
    </div>
  </div>
</section>

<script>
  const $ = (id: string) => document.getElementById(id)!;
  const fmtBytes = (b: number) => {
    if (!b) return '0 B';
    const u = ['B','KB','MB','GB','TB']; let i = 0; let n = b;
    while (n >= 1024 && i < u.length-1) { n /= 1024; i++; }
    return n.toFixed(1) + ' ' + u[i];
  };
  async function load() {
    try {
      const r = await fetch('/api/stats');
      const d = await r.json();
      $('stModels').textContent = d.total_models + ' 个';
      $('stWorkflows').textContent = d.total_workflows + ' 个';
      $('stMigrated').textContent = d.migrated_count + ' 次';
      $('stFailed').textContent = d.failed_count + ' 次';

      const rm = $('recentMig');
      rm.innerHTML = d.recent_migrations.length
        ? d.recent_migrations.map((m: any) => {
            const ok = m.status === 'success';
            const cls = ok ? 'ok' : (m.status === 'failed' ? 'fail' : 'pending');
            return `<tr><td class="mono">${m.rel_path.split('/').pop()}</td>
                    <td><span class="badge ${cls}">${m.status}</span></td>
                    <td class="muted">${m.ts}</td></tr>`;
          }).join('')
        : '<tr><td colspan="3" class="muted">暂无记录</td></tr>';

      const rw = $('recentWf');
      rw.innerHTML = d.recent_workflows.length
        ? d.recent_workflows.map((w: any) =>
            `<tr><td>${w.name}</td><td class="mono">${w.model_count}</td>
             <td class="muted">${w.imported_at}</td></tr>`).join('')
        : '<tr><td colspan="3" class="muted">暂无记录</td></tr>';
    } catch (e) { console.error(e); }
  }
  document.addEventListener('tab:activated', (e: any) => {
    if (e.detail.id === 'dashboard') load();
  });
  load();
</script>
```

### 3.5 Tab 2 — `components/tab/Paths.astro`

```astro
---
---
<section class="tab-panel hidden" data-panel="paths">
  <div class="card">
    <h2>路径配置</h2>
    <div class="grid cols-2">
      <div><label>ComfyUI 模型根目录</label>
        <input id="pComfy" type="text" placeholder="D:\working\ComfyUI" /></div>
      <div><label>SD 模型根目录</label>
        <input id="pSd" type="text" placeholder="D:\working\SD" /></div>
      <div><label>迁移模型存储目标根目录</label>
        <input id="pTarget" type="text" placeholder="E:\working\ComfyUI" /></div>
      <div><label>工作流 JSON 目录</label>
        <input id="pWfJson" type="text" placeholder="D:\working\ComfyUI\user\default\workflows" /></div>
      <div><label>SD 工作流目录（可选）</label>
        <input id="pSdWf" type="text" placeholder="D:\working\SD\workflows" /></div>
    </div>
    <div style="margin-top:14px;display:flex;gap:10px">
      <button id="saveCfg">保存路径</button>
      <button id="scanWf" class="ghost">扫描工作流目录并入库</button>
    </div>
    <p id="cfgHint" class="muted mono" style="margin:8px 0 0"></p>
  </div>

  <div class="card">
    <h2>拖拽上传工作流 JSON（自动解析模型）</h2>
    <div class="dropzone" id="dz">
      <div style="font-size:28px">📥</div>
      <div>将 .json 文件拖到此处，或 <u>点击选择</u></div>
    </div>
    <input id="fileInput" type="file" accept=".json" multiple class="hidden" />
    <div id="uploadResults" style="margin-top:14px"></div>
  </div>
</section>

<script>
  const $ = (id: string) => document.getElementById(id)!;

  async function loadCfg() {
    const r = await fetch('/api/config');
    const c = await r.json();
    ($('pComfy') as HTMLInputElement).value = c.comfyui_models_root || '';
    ($('pSd') as HTMLInputElement).value = c.sd_models_root || '';
    ($('pTarget') as HTMLInputElement).value = c.migrate_target_root || '';
    ($('pWfJson') as HTMLInputElement).value = c.workflow_json_dir || '';
    ($('pSdWf') as HTMLInputElement).value = c.sd_workflow_dir || '';
  }

  $('saveCfg')!.addEventListener('click', async () => {
    const body = {
      comfyui_models_root: ($('pComfy') as HTMLInputElement).value.trim(),
      sd_models_root: ($('pSd') as HTMLInputElement).value.trim(),
      migrate_target_root: ($('pTarget') as HTMLInputElement).value.trim(),
      workflow_json_dir: ($('pWfJson') as HTMLInputElement).value.trim(),
      sd_workflow_dir: ($('pSdWf') as HTMLInputElement).value.trim(),
    };
    const r = await fetch('/api/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    $('cfgHint').textContent = r.ok ? '✔ 已保存' : '✘ 保存失败';
  });

  $('scanWf')!.addEventListener('click', async () => {
    $('cfgHint').textContent = '扫描中…';
    const r = await fetch('/api/workflows/scan', { method: 'POST' });
    const d = await r.json();
    if (!r.ok) { $('cfgHint').textContent = '✘ ' + d.detail; return; }
    $('cfgHint').textContent =
      `✔ 新导入 ${d.imported_count} 个，跳过 ${d.skipped} 个`;
    document.dispatchEvent(new CustomEvent('workflows:changed'));
  });

  // —— 拖拽上传 ——
  const dz = $('dz'), fi = $('fileInput') as HTMLInputElement;
  dz.addEventListener('click', () => fi.click());
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('over'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('over'));
  dz.addEventListener('drop', async e => {
    e.preventDefault(); dz.classList.remove('over');
    await uploadFiles(Array.from(e.dataTransfer!.files));
  });
  fi.addEventListener('change', async () => {
    await uploadFiles(Array.from(fi.files || []));
    fi.value = '';
  });

  async function uploadFiles(files: File[]) {
    const out = $('uploadResults');
    out.innerHTML = '';
    for (const f of files) {
      if (!f.name.toLowerCase().endsWith('.json')) continue;
      const fd = new FormData(); fd.append('file', f);
      const r = await fetch('/api/upload', { method: 'POST', body: fd });
      const d = await r.json();
      if (!r.ok) {
        out.innerHTML += `<div class="card"><b>${f.name}</b> — <span class="badge fail">失败</span>
          <div class="muted mono">${d.detail}</div></div>`;
        continue;
      }
      const tree = buildTree(d.workflow.tree);
      out.innerHTML += `<div class="card">
        <b>${f.name}</b> — <span class="badge ok">解析成功</span>
        <span class="muted mono">（${d.model_count} 个模型）</span>
        <div class="tree mono" style="margin-top:8px">${tree}</div>
      </div>`;
    }
    document.dispatchEvent(new CustomEvent('workflows:changed'));
  }

  function buildTree(tree: any[]): string {
    let html = '';
    for (const g of tree) {
      html += `<div class="folder">│ ├── 📂 ${g.folder}/</div>`;
      for (const f of g.files) {
        html += `<div class="file">│ │ ├── ${f.name}</div>`;
      }
    }
    return html || '<div class="muted">未识别到模型引用</div>';
  }

  document.addEventListener('tab:activated', (e: any) => {
    if (e.detail.id === 'paths') loadCfg();
  });
  loadCfg();
</script>
```

### 3.6 Tab 3 — `components/tab/Workflows.astro`（含树形展开）

```astro
---
---
<section class="tab-panel hidden" data-panel="workflows">
  <div class="card">
    <h2>工作流列表（点击行展开模型树）</h2>
    <div style="display:flex;gap:10px;margin-bottom:12px">
      <button id="refreshWf" class="ghost small">刷新</button>
      <span class="muted mono" id="wfCount"></span>
    </div>
    <table>
      <thead><tr>
        <th style="width:24px"></th><th>工作流名称</th><th>模型数</th><th>路径</th><th>导入时间</th>
      </tr></thead>
      <tbody id="wfBody"><tr><td colspan="5" class="muted">加载中…</td></tr></tbody>
    </table>
  </div>
</section>

<script>
  const $ = (id: string) => document.getElementById(id)!;

  async function load() {
    const r = await fetch('/api/workflows');
    const wfs = await r.json();
    $('wfCount').textContent = `共 ${wfs.length} 个工作流`;
    const tb = $('wfBody');
    if (!wfs.length) {
      tb.innerHTML = '<tr><td colspan="5" class="muted">暂无工作流，请到「路径配置」上传或扫描</td></tr>';
      return;
    }
    tb.innerHTML = wfs.map((wf: any) => {
      const wfId = `wf-${wf.id}`;
      const modelCount = wf.models?.length || 0;
      return `
        <tr class="wf-row" data-target="${wfId}">
          <td><span class="caret">▶</span></td>
          <td><b>${wf.name}</b></td>
          <td class="mono">${modelCount}</td>
          <td class="mono muted" style="max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
            ${wf.file_path}</td>
          <td class="muted">${wf.imported_at}</td>
        </tr>
        <tr class="hidden" id="${wfId}"><td colspan="5">
          <div class="wf-tree">${renderTree(wf.tree, wf.id)}</div>
        </td></tr>`;
    }).join('');

    tb.querySelectorAll<HTMLElement>('.wf-row').forEach(row => {
      row.addEventListener('click', () => {
        const target = row.dataset.target!;
        const detail = document.getElementById(target)!;
        detail.classList.toggle('hidden');
        row.classList.toggle('wf-expanded');
      });
    });

    // 每个模型行绑定"迁移"按钮
    tb.querySelectorAll<HTMLButtonElement>('button.migrate-btn').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        await handleMigrate(
          Number(btn.dataset.wf),
          Number(btn.dataset.model),
          btn.dataset.rel!
        );
      });
    });
  }

  function renderTree(tree: any[], wfId: number): string {
    if (!tree?.length) return '<div class="muted">未识别到模型</div>';
    let html = '';
    tree.forEach((g: any, gi: number) => {
      const lastG = gi === tree.length - 1;
      const gPre = lastG ? '└──' : '├──';
      html += `<div class="folder">${gPre} 📂 ${g.folder}/</div>`;
      g.files.forEach((f: any, fi: number) => {
        const lastF = fi === g.files.length - 1;
        const fPre = lastG ? '    ' : '│   ';
        const fBr  = lastF ? '└──' : '├──';
        const size = (f.size / 1048576).toFixed(1);
        html += `<div class="file">${fPre}${fBr} 
          <span class="mono">${f.name}</span>
          <span class="muted mono">(${size} MB)</span>
          <button class="ghost small migrate-btn" 
            data-wf="${wfId}" data-model="${f.rel_path ? '' : ''}"
            data-rel="${f.rel_path}" data-relpath="${f.rel_path}"
            style="margin-left:8px">迁移</button>
        </div>`;
      });
    });
    return html;
  }

  // 迁移按钮需要 model_id（用于分析），从 rel_path 反查
  async function handleMigrate(wfId: number, _modelId: number, relPath: string) {
    // 用 rel_path 反查 model_id（后端 analyze 需要 id）
    const rAll = await fetch('/api/workflows');
    const wfs = await rAll.json();
    let mid: number | null = null;
    for (const wf of wfs) {
      if (wf.id !== wfId) continue;
      const m = (wf.models || []).find((x: any) => x.rel_path === relPath);
      if (m) { mid = m.id; break; }
    }
    if (!mid) { alert('找不到该模型 ID'); return; }

    const r = await fetch(`/api/migrate/analyze?model_id=${mid}&workflow_id=${wfId}`);
    if (!r.ok) { alert('分析失败'); return; }
    const plan = await r.json();

    const needsConfirm = plan.shared_with.length > 0 || plan.candidate_models.length > 1;
    if (!needsConfirm) {
      await doMigrate([relPath]);
      return;
    }
    showModal(plan);
  }

  function showModal(plan: any) {
    let overlay = document.getElementById('migrateOverlay');
    if (overlay) overlay.remove();
    overlay = document.createElement('div');
    overlay.id = 'migrateOverlay';
    overlay.className = 'overlay';
    overlay.innerHTML = `
      <div class="modal">
        <h3>确认迁移</h3>
        <p class="muted" style="font-size:13px">
          ${plan.shared_with.length
            ? `该模型还被 <b>${plan.shared_with.length}</b> 个工作流使用：`
            : '本工作流引用了多个模型，请勾选本次迁移的项：'}
        </p>
        ${plan.shared_with.length
          ? `<div class="muted mono" style="font-size:12px;margin-bottom:10px">
               ${plan.shared_with.map((w: any) => '• ' + w.name).join('<br>')}
             </div>`
          : ''}
        <div class="list">
          ${plan.candidate_models.map((m: any) => `
            <label>
              <input type="checkbox" value="${m.rel_path}" 
                     ${m.id === plan.target.id ? 'checked' : ''} />
              <span class="mono">${m.rel_path}</span>
              ${m.id === plan.target.id ? '<span class="badge ok">当前</span>' : ''}
            </label>
          `).join('')}
        </div>
        <div class="actions">
          <button class="ghost" id="migCancel">取消</button>
          <button id="migOk">确认迁移</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    const style = document.createElement('style');
    style.textContent = `
      .overlay { position:fixed; inset:0; background:rgba(0,0,0,.45);
                 display:flex; align-items:center; justify-content:center; z-index:100; }
      .modal { background:#fff; border-radius:12px; padding:20px 24px; width:640px;
               max-height:80vh; overflow:auto; }
      .modal h3 { margin:0 0 10px; font-size:16px; }
      .modal .list { border:1px solid #eef; border-radius:8px; overflow:hidden; margin:12px 0; }
      .modal .list label { display:flex; gap:8px; align-items:center; padding:9px 12px;
                           font-size:13px; border-bottom:1px solid #f2f2f8; margin:0; cursor:pointer; }
      .modal .list label:last-child { border-bottom:none; }
      .modal .actions { display:flex; justify-content:flex-end; gap:10px; margin-top:12px; }
    `;
    document.head.appendChild(style);

    overlay.querySelector('#migCancel')!.addEventListener('click', () => overlay!.remove());
    overlay.querySelector('#migOk')!.addEventListener('click', async () => {
      const checked = Array.from(overlay!.querySelectorAll('input[type=checkbox]:checked'))
        .map((el: any) => el.value);
      overlay!.remove();
      if (checked.length) await doMigrate(checked);
    });
  }

  async function doMigrate(relPaths: string[]) {
    const r = await fetch('/api/migrate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rel_paths: relPaths })
    });
    const d = await r.json();
    if (!r.ok) { alert('迁移失败: ' + d.detail); return; }
    showResult(d.results);
    document.dispatchEvent(new CustomEvent('tab:activated', { detail: { id: 'logs' }}));
  }

  function showResult(results: any[]) {
    let ov = document.getElementById('resultOverlay');
    if (ov) ov.remove();
    ov = document.createElement('div');
    ov.id = 'resultOverlay';
    ov.className = 'overlay';
    ov.innerHTML = `
      <div class="modal">
        <h3>迁移结果</h3>
        <div class="list">
          ${results.map((r: any) => `
            <label>
              <span class="badge ${r.status === 'success' ? 'ok' : 'fail'}">${r.status}</span>
              <span class="mono">${r.rel_path}</span>
              <span class="muted" style="font-size:12px">${r.message}</span>
            </label>`).join('')}
        </div>
        <div class="actions"><button id="resClose">关闭</button></div>
      </div>`;
    document.body.appendChild(ov);
    ov.querySelector('#resClose')!.addEventListener('click', () => ov!.remove());
  }

  $('refreshWf')!.addEventListener('click', load);
  document.addEventListener('workflows:changed', load);
  document.addEventListener('tab:activated', (e: any) => {
    if (e.detail.id === 'workflows') load();
  });
  load();
</script>
```

### 3.7 Tab 4 — `components/tab/Logs.astro`

```astro
---
---
<section class="tab-panel hidden" data-panel="logs">
  <div class="card">
    <h2>迁移日志</h2>
    <div style="display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
      <select id="logFilter" style="padding:7px 10px;border:1px solid #e6e8ef;border-radius:8px">
        <option value="">全部</option>
        <option value="success">成功</option>
        <option value="failed">失败</option>
        <option value="pending">等待中</option>
      </select>
      <button id="refreshLogs" class="ghost small">刷新</button>
      <button id="clearLogs" class="danger small">清空日志</button>
      <span class="muted mono" id="logCount"></span>
    </div>
    <table>
      <thead><tr>
        <th>时间</th><th>相对路径</th><th>源</th><th>目标</th><th>状态</th><th>信息</th><th></th>
      </tr></thead>
      <tbody id="logBody"><tr><td colspan="7" class="muted">加载中…</td></tr></tbody>
    </table>
  </div>
</section>

<script>
  const $ = (id: string) => document.getElementById(id)!;

  async function load() {
    const status = ($('logFilter') as HTMLSelectElement).value;
    const r = await fetch(`/api/logs?status=${status}`);
    const logs = await r.json();
    $('logCount').textContent = `共 ${logs.length} 条`;
    const tb = $('logBody');
    if (!logs.length) {
      tb.innerHTML = '<tr><td colspan="7" class="muted">暂无日志</td></tr>';
      return;
    }
    tb.innerHTML = logs.map((l: any) => {
      const cls = l.status === 'success' ? 'ok' : (l.status === 'failed' ? 'fail' : 'pending');
      return `
        <tr>
          <td class="mono muted">${l.ts}</td>
          <td class="mono">${l.rel_path}</td>
          <td class="mono muted" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
              title="${l.src_path}">${l.src_path}</td>
          <td class="mono muted" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
              title="${l.dst_path}">${l.dst_path}</td>
          <td><span class="badge ${cls}">${l.status}</span></td>
          <td class="muted" style="font-size:12px">${l.message || ''}</td>
          <td><button class="danger small" data-id="${l.id}">删除</button></td>
        </tr>`;
    }).join('');

    tb.querySelectorAll<HTMLButtonElement>('button[data-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm('删除该日志？')) return;
        await fetch(`/api/logs/${btn.dataset.id}`, { method: 'DELETE' });
        load();
      });
    });
  }

  $('logFilter')!.addEventListener('change', load);
  $('refreshLogs')!.addEventListener('click', load);
  $('clearLogs')!.addEventListener('click', async () => {
    if (!confirm('清空全部日志？此操作不可恢复。')) return;
    const status = ($('logFilter') as HTMLSelectElement).value;
    await fetch(`/api/logs?status=${status}`, { method: 'DELETE' });
    load();
  });

  document.addEventListener('tab:activated', (e: any) => {
    if (e.detail.id === 'logs') load();
  });
  load();
</script>
```

### 3.8 `src/pages/index.astro`

```astro
---
import Layout from '../layouts/Layout.astro';
import Tabs from '../components/Tabs.astro';
import Dashboard from '../components/tab/Dashboard.astro';
import Paths from '../components/tab/Paths.astro';
import Workflows from '../components/tab/Workflows.astro';
import Logs from '../components/tab/Logs.astro';
---
<Layout title="ComfyUI 模型迁移管理">
  <h1>ComfyUI 模型迁移管理</h1>
  <p class="muted" style="margin:4px 0 0">工作流解析 · 引用分析 · 一键安全迁移</p>
  <Tabs />
  <Dashboard />
  <Paths />
  <Workflows />
  <Logs />
</Layout>
```

---

## 四、全流程使用演示

### Step 1 · 路径配置

```
┌──────────────────────────────────────────────────┐
│ ComfyUI 模型根目录:    D:\working\ComfyUI        │
│ SD 模型根目录:         D:\working\SD             │
│ 迁移模型存储目标:      E:\working\ComfyUI        │
│ 工作流 JSON 目录:      D:\...\user\default\workflows │
│ SD 工作流目录:         D:\working\SD\workflows   │
└──────────────────────────────────────────────────┘
     [保存路径]   [扫描工作流目录并入库]
```

### Step 2 · 拖拽上传

将 `3d_pixal3d_trellis2_image_to_model.json` 拖到虚线框，自动解析并展示：

```
3d_pixal3d_trellis2_image_to_model.json — ✔ 解析成功（9 个模型）
│ ├── 📂 diffusion_models/
│ │ ├── trellis_2_int8_convrot.safetensors
│ │ └── pixal3d_int8_convrot.safetensors
│ ├── 📂 vae/
│ │ ├── trellis_2_shape_vae_bf16.safetensors
│ │ └── trellis_2_texture_vae_bf16.safetensors
│ ├── 📂 clip_vision/
│ │ └── dino_v3_L_naf_fp32.safetensors
│ └── 📂 background_removal/
│     └── birefnet.safetensors
```

### Step 3 · 工作流页面展开

点击 `3d_pixal3d_trellis2_image_to_model.json` 行，展开树形（完全匹配你的演示格式），每个 `.safetensors` 后带**迁移**按钮。

点击「迁移」→ 后端分析 → 若被 `3d_pixal3d_multi_views.json` 共享 → **弹窗**：

```
该模型还被 1 个工作流使用：
• 3d_pixal3d_multi_views.json

☑ models/diffusion_models/trellis_2_int8_convrot.safetensors  [当前]
☐ models/vae/trellis_2_shape_vae_bf16.safetensors
☐ models/clip_vision/dino_v3_L_naf_fp32.safetensors
...
        [取消]  [确认迁移]
```

### Step 4 · 迁移执行

对每个勾选文件：
```
① dst.parent.mkdir(parents=True, exist_ok=True)  → 创建 E:\...\models\diffusion_models\
② shutil.copy2(src, tmp) → os.replace(tmp, dst)  → 原子复制
③ INSERT INTO migrations → 记录日志
```
源 `D:\...\triposplat_fp16.safetensors` **保留不动**。

### Step 5 · 日志页面

自动刷新（通过 `tab:activated` 事件），可筛选/删除/清空。

---

## 五、数据流与关键点回顾

| 需求 | 实现位置 |
|---|---|
| 主页面统计 | `routers/stats.py` + `Dashboard.astro` |
| 5 个路径配置 | `schemas.PathConfig` + `Paths.astro` |
| 拖拽上传 + 解析 | `routers/upload.py` + `Paths.astro` 的 `dropzone` |
| 工作流树形展开 | `routers/workflows.py::_tree` + `Workflows.astro::renderTree` |
| 共享模型弹窗勾选 | `routers/analyze.py` + `Workflows.astro::showModal` |
| 原子迁移 + 自动建目录 | `services/mover.py` |
| 源文件保留 | `shutil.copy2`（从不 `move`） |
| 迁移日志 CRUD | `routers/logs.py` + `Logs.astro` |

---

## 六、可选增强（后续迭代）

- **WebSocket 实时进度**：迁移大文件（数 GB）时用 `asyncio.Queue` + WS 推送百分比。
- **SHA256 校验**：迁移完成后比对源/目标哈希，写入 `migrations` 表。
- **工作流版本对比**：同一 `name` 的多次上传，保留历史 `workflow_versions` 表。
- **PyInstaller 单文件打包**：
  ```bash
  npm run build                      # 前端产物 dist/
  pyinstaller -F -n migrator \
    --add-data "dist;dist" \
    --add-data "config.json;." main.py
  ```
  在 `main.py` 末尾挂 `app.mount("/", StaticFiles(directory="dist", html=True))` 即可单 exe 双击运行。