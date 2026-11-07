from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from db import get_conn, now_str
from schemas import PathConfig
from services.catalog import ModelCatalog
from services.fingerprint import quick_fingerprint
from services.mover import CopyCancelled, UnsafePathError, copy_with_progress, target_path
from services.paths import to_posix_path

_ROOT_FIELDS = {
    "comfyui": "comfyui_models_root",
    "sd": "sd_models_root",
    "target": "migrate_target_root",
}
_ROOT_LABELS = {
    "comfyui": "ComfyUI 模型根目录",
    "sd": "SD 模型根目录",
    "target": "迁移目标根目录",
}


@dataclass
class MigrationTask:
    id: str
    config: PathConfig
    destination: str
    move: bool
    destination_root: str
    overwrite: bool = False
    total_bytes: int = 0
    files: list[dict] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    status: str = "queued"
    phase: str = "准备中"
    message: str = ""
    copied_bytes: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def update(self, **kwargs) -> None:
        with self.lock:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def snapshot(self) -> dict:
        with self.lock:
            elapsed = 0.0
            if self.started_at:
                end = self.finished_at or time.time()
                elapsed = max(end - self.started_at, 0.001)
            speed = self.copied_bytes / elapsed if elapsed else 0.0
            if self.total_bytes > 0:
                percent = min(self.copied_bytes / self.total_bytes, 1.0)
            else:
                percent = 1.0 if self.status in ("done", "cancelled") else 0.0
            if self.status == "done":
                percent = 1.0
            eta = (
                max(self.total_bytes - self.copied_bytes, 0) / speed
                if speed > 0
                else 0
            )
            return {
                "id": self.id,
                "status": self.status,
                "phase": self.phase,
                "message": self.message,
                "destination": self.destination,
                "destination_root": to_posix_path(self.destination_root),
                "move": self.move,
                "overwrite": self.overwrite,
                "total_bytes": self.total_bytes,
                "copied_bytes": self.copied_bytes,
                "percent": percent,
                "speed": speed,
                "eta": eta,
                "files": [dict(item) for item in self.files],
                "results": [dict(item) for item in self.results],
            }


class TaskManager:
    def __init__(self) -> None:
        self._tasks: OrderedDict[str, MigrationTask] = OrderedDict()
        self._lock = threading.Lock()

    def create(
        self,
        config: PathConfig,
        rel_paths: list[str],
        destination: str,
        move: bool,
        plan: dict,
        overwrite: bool = False,
    ) -> str:
        task_id = uuid.uuid4().hex
        destination_root = getattr(config, _ROOT_FIELDS[destination]).strip()
        task = MigrationTask(
            id=task_id,
            config=config,
            destination=destination,
            move=move,
            destination_root=destination_root,
            overwrite=overwrite,
            total_bytes=int(plan.get("need_bytes") or 0),
            files=[
                {
                    "rel_path": item["rel_path"],
                    "size": item["size"],
                    "copied_bytes": 0,
                    "status": "pending",
                    "message": "",
                }
                for item in plan.get("files", [])
            ],
        )
        with self._lock:
            self._tasks[task_id] = task
            self._prune_locked()
        thread = threading.Thread(target=self._run, args=(task,), daemon=True)
        thread.start()
        return task_id

    def get(self, task_id: str) -> MigrationTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None:
            return False
        with task.lock:
            if task.status not in ("queued", "running"):
                return False
            task.cancel_event.set()
        return True

    def _prune_locked(self) -> None:
        finished = [
            key
            for key, task in self._tasks.items()
            if task.status in ("done", "failed", "cancelled")
        ]
        for key in finished[: max(0, len(finished) - 40)]:
            self._tasks.pop(key, None)

    def _run(self, task: MigrationTask) -> None:
        task.update(status="running", phase="复制中", started_at=time.time())
        config = task.config
        source_roots = [
            config.comfyui_models_root,
            config.sd_models_root,
            config.migrate_target_root,
        ]
        catalog = ModelCatalog(source_roots)
        destinations = ModelCatalog([task.destination_root]).refresh()

        try:
            with get_conn() as conn:
                for index, item in enumerate(task.files):
                    if task.cancel_event.is_set():
                        self._cancel_remaining(task, index)
                        break
                    self._process_file(
                        task, conn, catalog, destinations, item, index
                    )
                    conn.commit()
            with task.lock:
                if task.status == "running":
                    task.status = "done"
                    task.phase = "已完成"
                task.finished_at = time.time()
        except Exception as exc:  # noqa: BLE001 - task state must capture failures
            with task.lock:
                task.status = "failed"
                task.phase = "失败"
                task.message = str(exc)
                task.finished_at = time.time()

    def _process_file(
        self,
        task: MigrationTask,
        conn,
        catalog: ModelCatalog,
        destinations: ModelCatalog,
        item: dict,
        index: int,
    ) -> None:
        rel_path = item["rel_path"]
        timestamp = now_str()
        resolved = catalog.resolve(rel_path)
        source = to_posix_path(resolved.abs_path) if resolved.abs_path else ""
        destination = ""
        status = "failed"
        message = "未找到源文件，请检查模型根目录配置"

        if resolved.found and resolved.abs_path is not None:
            try:
                destination_path = target_path(task.destination_root, resolved.rel_path)
                destination = to_posix_path(destination_path)
            except (OSError, UnsafePathError, ValueError) as exc:
                message = str(exc)
            else:
                target_hit = destinations.get(resolved.rel_path)
                same_path = resolved.abs_path.resolve() == destination_path.resolve()
                item["dst_path"] = destination
                same_content = False
                if target_hit is not None and target_hit.size == resolved.size:
                    try:
                        same_content = quick_fingerprint(
                            resolved.abs_path
                        ) == quick_fingerprint(target_hit.abs_path)
                    except OSError:
                        same_content = False

                if same_path:
                    status = "success"
                    message = "源与目标为同一位置，未执行复制"
                    self._mark(task, item, "skipped", destination, message)
                elif target_hit is not None and same_content and not task.overwrite:
                    status = "success"
                    message = "目标已存在且内容一致，已跳过复制"
                    if task.move:
                        error = self._delete_source(resolved.abs_path)
                        if error:
                            status = "failed"
                            message = f"目标已存在，但删除源文件失败：{error}"
                        else:
                            message = "目标已存在且内容一致，源文件已剪贴删除"
                    self._mark(
                        task,
                        item,
                        "success" if status == "success" else "failed",
                        destination,
                        message,
                    )
                elif target_hit is not None and not same_content and not task.overwrite:
                    status = "failed"
                    message = "目标已存在不同版本，未勾选覆盖，源文件与目标均未改动"
                    self._mark(task, item, "failed", destination, message)
                else:
                    item["status"] = "copying"
                    item["copied_bytes"] = 0
                    base_copied = task.copied_bytes

                    def on_progress(copied: int, total: int) -> None:
                        with task.lock:
                            item["copied_bytes"] = copied
                            task.copied_bytes = base_copied + copied
                            task.phase = "复制中"

                    try:
                        outcome = copy_with_progress(
                            resolved.abs_path,
                            destination_path,
                            on_progress,
                            task.cancel_event,
                        )
                    except CopyCancelled:
                        item["status"] = "cancelled"
                        item["message"] = "任务已取消，临时文件已清理"
                        status = "cancelled"
                        message = item["message"]
                        with task.lock:
                            task.status = "cancelled"
                            task.phase = "已取消"
                        self._cancel_remaining(task, index + 1)
                    except (OSError, UnsafePathError, ValueError) as exc:
                        message = str(exc)
                        item["status"] = "failed"
                        item["message"] = message
                    else:
                        status = "success"
                        message = (
                            "已复制到目标目录"
                            if outcome == "copied"
                            else "目标已存在，已覆盖"
                        )
                        if task.move:
                            error = self._delete_source(resolved.abs_path)
                            if error:
                                status = "failed"
                                message = f"已复制到目标，但删除源文件失败：{error}"
                            else:
                                message = "已剪贴到目标目录（源文件已删除）"
                        with task.lock:
                            task.copied_bytes = base_copied + resolved.size
                            item["copied_bytes"] = resolved.size
                        item["status"] = "success" if status == "success" else "failed"
                        item["message"] = message
                        conn.execute(
                            "UPDATE models SET size = ?, found = 1 WHERE rel_path = ?",
                            (resolved.size, resolved.rel_path),
                        )

        if status != "cancelled":
            item["status"] = (
                item["status"]
                if item["status"] in ("success", "skipped", "failed")
                else ("success" if status == "success" else "failed")
            )
            item["message"] = message
            conn.execute(
                """
                INSERT INTO migrations (rel_path, src_path, dst_path, status, message, ts)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (rel_path, source, destination, status, message, timestamp),
            )
            task.results.append(
                {
                    "rel_path": rel_path,
                    "src_path": source,
                    "dst_path": destination,
                    "destination": task.destination,
                    "move": task.move,
                    "overwrite": task.overwrite,
                    "status": status,
                    "message": message,
                }
            )

    def _mark(
        self,
        task: MigrationTask,
        item: dict,
        item_status: str,
        destination: str,
        message: str,
    ) -> None:
        item["status"] = item_status
        item["message"] = message
        item["dst_path"] = destination

    def _delete_source(self, path) -> str:
        try:
            path.unlink()
        except OSError as exc:
            return str(exc)
        return ""

    def _cancel_remaining(self, task: MigrationTask, start: int) -> None:
        with task.lock:
            task.status = "cancelled"
            task.phase = "已取消"
            for remaining in task.files[start:]:
                if remaining["status"] == "pending":
                    remaining["status"] = "cancelled"
                    remaining["message"] = "任务已取消"


tasks = TaskManager()
