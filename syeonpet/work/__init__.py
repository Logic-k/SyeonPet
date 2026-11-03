"""SyeonPet Direct Work Assistant and Productivity Package."""
from syeonpet.work.assistant_core import (
    SYSTEM,
    create_direct_draft,
    detect_work_intent,
    generate_offline_deliverable,
)
from syeonpet.work.bag_ui import BagWorkspaceWidget
from syeonpet.work.doc_export import (
    export_deliverable,
    export_to_csv,
    export_to_html,
    export_to_markdown,
    export_to_txt,
)
from syeonpet.work.tasks_memo import BagTaskManager, TaskItem
from syeonpet.work.web_ai_bridge import (
    WEB_AI_TARGETS,
    copy_and_open_web_ai,
    get_target_url,
    get_web_ai_targets,
)

__all__ = [
    "BagTaskManager",
    "BagWorkspaceWidget",
    "SYSTEM",
    "TaskItem",
    "WEB_AI_TARGETS",
    "copy_and_open_web_ai",
    "create_direct_draft",
    "detect_work_intent",
    "export_deliverable",
    "export_to_csv",
    "export_to_html",
    "export_to_markdown",
    "export_to_txt",
    "generate_offline_deliverable",
    "get_target_url",
    "get_web_ai_targets",
]
