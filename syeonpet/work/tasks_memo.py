"""Syeon's Bag Tasks & Quick Memo Model and Storage.

Provides atomic, corruption-safe persistence for tasks and quick office notes
under %LOCALAPPDATA%\\SyeonPet\\bag_tasks.json.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import tempfile
import uuid


def get_default_storage_path() -> Path:
    """Return default path for bag tasks and memo under local appdata."""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        base = Path(local_appdata) / "SyeonPet"
    else:
        base = Path.home() / ".syeonpet"
    base.mkdir(parents=True, exist_ok=True)
    return base / "bag_tasks.json"


class TaskItem:
    """Represents a single task in Syeon's bag."""

    def __init__(
        self,
        title: str,
        due_date: str = "",
        done: bool = False,
        task_id: str | None = None,
        created_at: str | None = None,
    ) -> None:
        self.id = task_id or uuid.uuid4().hex[:8]
        self.title = title.strip()
        self.due_date = due_date.strip()
        self.done = done
        self.created_at = created_at or datetime.datetime.now().isoformat()

    @property
    def status_badge(self) -> str:
        """Return Korean status badge for novice display."""
        if self.done:
            return "✅ 완료"
        if not self.due_date:
            return "📌 할 일"
        today = datetime.date.today().isoformat()
        if self.due_date < today:
            return "🚨 기한 지남"
        if self.due_date == today:
            return "🔥 오늘 마감"
        return "📅 예정"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "due_date": self.due_date,
            "done": self.done,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskItem:
        return cls(
            title=data.get("title", ""),
            due_date=data.get("due_date", ""),
            done=bool(data.get("done", False)),
            task_id=data.get("id"),
            created_at=data.get("created_at"),
        )


class BagTaskManager:
    """Atomic storage manager for bag tasks and quick sticky memo."""

    def __init__(self, storage_path: Path | str | None = None) -> None:
        self.path = Path(storage_path) if storage_path else get_default_storage_path()
        self.tasks: list[TaskItem] = []
        self.quick_memo: str = ""
        self.load()

    def load(self) -> None:
        """Load tasks and memo from disk, falling back gracefully on error."""
        self.tasks = []
        self.quick_memo = ""
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                raw_tasks = data.get("tasks", [])
                self.tasks = [TaskItem.from_dict(t) for t in raw_tasks if isinstance(t, dict)]
                self.quick_memo = str(data.get("quick_memo", ""))
        except Exception:
            # Corruption fallback: preserve existing memory state
            pass

    def save(self) -> None:
        """Atomically persist state to disk via temporary file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": datetime.datetime.now().isoformat(),
            "tasks": [t.to_dict() for t in self.tasks],
            "quick_memo": self.quick_memo,
        }
        raw_json = json.dumps(payload, ensure_ascii=False, indent=2)

        # Atomic replace in same directory
        tmp_path = self.path.parent / f"{self.path.name}.tmp.{uuid.uuid4().hex[:6]}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(raw_json)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            # Fallback direct write if atomic replace encounters platform locks
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write(raw_json)
            except Exception:
                pass

    def add_task(self, title: str, due_date: str = "") -> TaskItem:
        """Add a new task item and save."""
        task = TaskItem(title=title, due_date=due_date)
        self.tasks.append(task)
        self.save()
        return task

    def toggle_task(self, task_id: str) -> bool:
        """Toggle task completion state and save. Returns new done state."""
        for t in self.tasks:
            if t.id == task_id:
                t.done = not t.done
                self.save()
                return t.done
        return False

    def update_task(
        self,
        task_id: str,
        title: str | None = None,
        due_date: str | None = None,
        done: bool | None = None,
    ) -> bool:
        """Update fields of an existing task and save."""
        for t in self.tasks:
            if t.id == task_id:
                if title is not None:
                    t.title = title.strip()
                if due_date is not None:
                    t.due_date = due_date.strip()
                if done is not None:
                    t.done = done
                self.save()
                return True
        return False

    def delete_task(self, task_id: str) -> bool:
        """Delete task by id and save."""
        initial_len = len(self.tasks)
        self.tasks = [t for t in self.tasks if t.id != task_id]
        if len(self.tasks) < initial_len:
            self.save()
            return True
        return False

    def get_tasks(self) -> list[TaskItem]:
        """Return shallow copy of all tasks."""
        return list(self.tasks)

    def get_memo(self) -> str:
        """Return quick memo text."""
        return self.quick_memo

    def set_memo(self, memo: str) -> None:
        """Update and save quick memo."""
        self.quick_memo = memo
        self.save()
