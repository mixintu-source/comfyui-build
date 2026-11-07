export type Json = Record<string, any>;

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function escapeHtml(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPES[char] ?? char);
}

export function byId<T extends HTMLElement = HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) throw new Error(`缺少页面元素 #${id}`);
  return element as T;
}

export function fmtBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 100 || index === 0 ? 0 : 1)} ${units[index]}`;
}

export function fmtFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  return fmtBytes(bytes);
}

export async function api<T = any>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const text = await response.text();
  let data: any = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!response.ok) {
    const detail = data && typeof data === "object" ? data.detail : data;
    let message = `请求失败（HTTP ${response.status}）`;
    if (typeof detail === "string" && detail) {
      message = detail;
    } else if (Array.isArray(detail)) {
      message = detail
        .map((item: any) => item?.msg || item?.message || String(item))
        .join("；");
    } else if (detail && typeof detail === "object" && detail.message) {
      message = detail.message;
    }
    const error = new Error(message) as Error & { detail?: unknown };
    error.detail = detail;
    throw error;
  }
  return data as T;
}

export function statusBadge(status: string): string {
  const cls = status === "success" ? "ok" : status === "failed" ? "fail" : "pending";
  const label =
    status === "success" ? "成功" : status === "failed" ? "失败" : "等待中";
  return `<span class="badge ${cls}">${escapeHtml(label)}</span>`;
}

export function showTab(id: string): void {
  document.dispatchEvent(new CustomEvent("tab:show", { detail: { id } }));
}

export function toast(message: string, kind: "ok" | "fail" = "ok"): void {
  const host = document.getElementById("toastHost");
  if (!host) return;
  const item = document.createElement("div");
  item.className = `toast ${kind}`;
  item.textContent = message;
  host.appendChild(item);
  window.setTimeout(() => item.remove(), 3600);
}

export function openOverlay(innerHtml: string): {
  root: HTMLElement;
  close: () => void;
} {
  const root = document.createElement("div");
  root.className = "overlay";
  root.innerHTML = `<div class="modal">${innerHtml}</div>`;
  document.body.appendChild(root);
  const close = () => root.remove();
  root.addEventListener("click", (event) => {
    if (event.target === root) close();
  });
  const onKey = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      close();
      document.removeEventListener("keydown", onKey);
    }
  };
  document.addEventListener("keydown", onKey);
  return { root, close };
}

export function basename(path: string): string {
  const parts = String(path ?? "").replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || path;
}

export async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = value;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      area.remove();
    }
  }
}

function fmtEta(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "--:--";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours
    ? `${hours}:${pad(minutes)}:${pad(secs)}`
    : `${pad(minutes)}:${pad(secs)}`;
}

const TASK_BADGES: Record<string, [string, string]> = {
  pending: ["pending", "等待"],
  copying: ["ok", "复制中"],
  success: ["ok", "成功"],
  skipped: ["pending", "跳过"],
  failed: ["fail", "失败"],
  cancelled: ["pending", "已取消"],
};

export async function runMigrationTask(payload: {
  rel_paths: string[];
  destination?: string;
  move?: boolean;
  overwrite?: boolean;
}): Promise<{ status: string; results: any[]; task: any }> {
  const { root } = openOverlay(`
    <h3>迁移任务</h3>
    <p class="hint" id="taskPhase">正在创建任务…</p>
    <div class="bar-wrap"><div class="bar" id="taskBar"></div></div>
    <div class="progress-meta mono" id="taskMeta">等待任务创建…</div>
    <div class="file-list scroll" id="taskFiles"></div>
    <div class="actions">
      <button class="danger" type="button" data-action="cancel">取消任务</button>
    </div>
  `);
  const phase = root.querySelector<HTMLElement>("#taskPhase")!;
  const bar = root.querySelector<HTMLElement>("#taskBar")!;
  const meta = root.querySelector<HTMLElement>("#taskMeta")!;
  const fileList = root.querySelector<HTMLElement>("#taskFiles")!;
  const cancelButton = root.querySelector<HTMLButtonElement>(
    '[data-action="cancel"]',
  )!;

  let taskId = "";
  cancelButton.addEventListener("click", async () => {
    if (!taskId || cancelButton.disabled) return;
    cancelButton.disabled = true;
    cancelButton.textContent = "正在取消…";
    try {
      await api(`/api/migrate/tasks/${taskId}/cancel`, { method: "POST" });
      phase.textContent = "正在取消，等待当前文件收尾…";
    } catch (error) {
      toast((error as Error).message, "fail");
      cancelButton.disabled = false;
      cancelButton.textContent = "取消任务";
    }
  });

  try {
    const created = await api("/api/migrate/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    taskId = created.task_id;
    phase.textContent = "任务已创建，等待开始…";

    while (true) {
      const task = await api(`/api/migrate/tasks/${taskId}`);
      const pct = Math.round((task.percent || 0) * 100);
      bar.style.width = `${pct}%`;
      phase.textContent = task.phase || "复制中";
      meta.textContent =
        `${pct}% · ${fmtBytes(task.copied_bytes || 0)} / ${fmtBytes(task.total_bytes || 0)}` +
        ` · ${fmtBytes(task.speed || 0)}/s · 剩余 ${fmtEta(task.eta || 0)}`;
      cancelButton.classList.toggle(
        "hidden",
        ["done", "failed", "cancelled"].includes(task.status),
      );
      fileList.innerHTML = (task.files || [])
        .map((item: any) => {
          const [cls, label] = TASK_BADGES[item.status] ?? ["pending", item.status];
          return `<div class="f">
            <span class="path">${escapeHtml(item.rel_path)}</span>
            <span class="badge ${cls}">${label}</span>
            <span class="muted mono">${fmtBytes(item.copied_bytes || 0)} / ${fmtBytes(item.size || 0)}</span>
          </div>`;
        })
        .join("");

      if (["done", "failed", "cancelled"].includes(task.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, 250));
        root.remove();
        return {
          status: task.status,
          results: task.results ?? [],
          task,
        };
      }
      await new Promise((resolve) => window.setTimeout(resolve, 300));
    }
  } catch (error) {
    root.remove();
    throw error;
  }
}
