"""Single command surface: thin transports delegate to these use cases."""

import asyncio
import inspect
import logging
import threading
from pathlib import Path

from pydantic import ConfigDict, ValidationError, validate_call

from ..infrastructure.database import ConflictError, Database
from .agent_runtime import AgentRuntime
from .assurance import AssuranceService
from .attachments import AttachmentService
from .design import DesignService
from .execution import ExecutionService
from .graph_editor import GraphEditor
from .planning import PlanningService
from .projects import ProjectService
from .research import ResearchService
from .settings import SettingsService

logger = logging.getLogger(__name__)


class Application:
    def __init__(self, data_dir: Path, secrets=None):
        self.db = Database(data_dir / "evograph.sqlite3")
        self.projects = ProjectService(self.db)
        self.settings = SettingsService(self.db, secrets)
        self.research = ResearchService(self.db, self.settings.secrets)
        self.planning = PlanningService(self.db, self.settings)
        self.execution = ExecutionService(self.db)
        self.assurance = AssuranceService(self.db, self.execution)
        self.graph = GraphEditor(self.db)
        self.attachments = AttachmentService(self.db)
        self.design = DesignService(self.db)
        self.agent = AgentRuntime(self)
        self._locks = {}
        self._lock_guard = threading.Lock()
        # A service method is registered once. HTTP and pywebview share this table.
        operations = {
            "projects.list": self.projects.list,
            "projects.create": self.projects.create,
            "projects.get": self.projects.get,
            "projects.update": self.projects.update,
            "projects.delete": self.projects.delete,
            "projects.restore": self.projects.restore,
            "projects.bootstrap": self.bootstrap,
            "projects.demo": self.demo,
            "settings.get": self.settings.get,
            "settings.save": self.settings.save,
            "settings.test": self.settings.test,
            "research.settings": self.research.settings,
            "research.configure": self.research.configure,
            "attachments.upload": self.attachments.upload,
            "attachments.read": self.attachments.read,
            "attachments.formats": self.attachments.formats,
            "design.update": self.design.update,
            "design.diagram": self.design.save_diagram,
            "agent.chat": self.planning.chat,  # Legacy API compatibility; new UI uses streaming tools.
            "plan.apply": self.planning.apply,
            "plan.discard": self.planning.discard,
            "baseline.refresh": self.execution.refresh,
            "milestone.start": self.execution.start,
            "milestone.release": self.execution.release,
            "milestone.verify": self.execution.verify,
            "verification.export": self.assurance.handoff,
            "verification.import": self.assurance.import_report,
            "milestone.obligation": self.execution.resolve_obligation,
            "graph.positions": self.execution.positions,
        }
        self.operations = {
            key: validate_call(config=ConfigDict(strict=True))(fn) for key, fn in operations.items()
        }

    def demo(self):
        return self.projects.demo(self.planning)

    def bootstrap(self):
        if not self.db.setting("welcome_initialized", False):
            if not self.db.list_projects(include_archived=True):
                self.demo()
            self.db.set_setting("welcome_initialized", True)
        return self.projects.list()

    def operation_lock(self, project_id):
        with self._lock_guard:
            return self._locks.setdefault(project_id, threading.Lock())

    async def dispatch(self, action: str, params: dict):
        if action not in self.operations:
            return {"ok": False, "error": {"code": "UNKNOWN_ACTION", "message": "未知操作"}}
        lock = None
        readonly = action in {"projects.list", "projects.get", "settings.get"}
        if not readonly:
            key = params.get("project_id", "__global__")
            with self._lock_guard:
                lock = self._locks.setdefault(key, threading.Lock())
            if not lock.acquire(blocking=False):
                return {
                    "ok": False,
                    "error": {"code": "BUSY", "message": "当前项目有操作正在运行，请稍后重试"},
                }
        try:
            fn = self.operations[action]
            if inspect.iscoroutinefunction(fn):
                result = await fn(**params)
            else:
                result = await asyncio.to_thread(fn, **params)
            if hasattr(result, "model_dump"):
                result = result.model_dump()
            return {"ok": True, "data": result}
        except ConflictError as exc:
            return {"ok": False, "error": {"code": "CONFLICT", "message": str(exc)}}
        except ValidationError as exc:
            problems = [
                ".".join(map(str, e["loc"])) + ": " + e["msg"]
                for e in exc.errors(include_input=False)
            ]
            return {
                "ok": False,
                "error": {"code": "VALIDATION", "message": "\n".join(problems)[:2000]},
            }
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": {"code": "INVALID_OPERATION", "message": str(exc)[:2000]}}
        except Exception as exc:
            # Never return credentials, Provider response bodies, or stack traces to the UI.
            logger.error("Action %s failed: %s", action, type(exc).__name__)
            return {
                "ok": False,
                "error": {"code": "INTERNAL", "message": "操作未完成，请检查网络连接和应用日志"},
            }
        finally:
            if lock:
                lock.release()
