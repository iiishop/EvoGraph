from ..domain.models import PlanProposal, Project
from ..domain.policies import acceptance, current_evidence, readiness
from ..infrastructure.repository import root_path


def project_view(project, db):
    data = project.model_dump()
    data["acceptance"] = acceptance(project)
    data["readiness"] = {m.id: readiness(project, m.id) for m in project.milestones}
    data["evidence_validity"] = {
        e.id: (
            "CURRENT"
            if project.baseline
            and e.baseline_id == project.baseline.id
            and project.baseline.complete
            and e.fingerprint == project.baseline.fingerprint
            else "STALE"
        )
        for e in project.evidence
    }
    data["verified_behaviors"] = [
        b.id for b in project.behaviors if current_evidence(project, b.id)
    ]
    data["messages"] = db.messages(project.id)
    data["events"] = db.events(project.id)
    return data


class ProjectService:
    def __init__(self, db):
        self.db = db

    def list(self):
        return [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "is_demo": p.is_demo,
                "milestone_count": len(p.milestones),
                "acceptance": acceptance(p),
            }
            for p in self.db.list_projects()
        ]

    def create(self, name: str, description: str = "", repository: str = "", request_id: str = ""):
        if not name.strip() or len(name) > 100:
            raise ValueError("项目名称必须为 1–100 字符")
        if repository:
            repository = str(root_path(repository))
        return self.db.create(
            Project(
                name=name.strip(),
                description=description[:4000],
                repository=repository,
                creation_key=request_id,
            )
        )

    def update(self, project_id: str, name: str, description: str, repository: str):
        p = self.db.get(project_id)
        if not name.strip() or len(name) > 100:
            raise ValueError("项目名称必须为 1–100 字符")
        if repository:
            repository = str(root_path(repository))
        if p.repository != repository:
            if any(m.status in {"IN_PROGRESS", "REVALIDATION_REQUIRED"} for m in p.milestones):
                raise ValueError("请先释放在途任务再更换仓库")
            if p.baselines:
                raise ValueError("已建立基线的项目不能更换仓库，请创建新项目")
        p.name, p.description, p.repository = name.strip(), description[:4000], repository
        return self.db.save(p, "project_updated")

    def get(self, project_id):
        return project_view(self.db.get(project_id), self.db)

    def delete(self, project_id: str):
        project = self.db.get(project_id)
        if any(m.lease_active for m in project.milestones):
            raise ValueError("请先释放已领取的里程碑，再删除项目")
        project.archived = True
        self.db.save(
            project, "project_deleted", "Removed from workspace; repository files are untouched"
        )
        return {"id": project.id, "deleted": True}

    def restore(self, project_id: str):
        project = self.db.get(project_id)
        for existing in self.db.list_projects():
            if project.repository and existing.repository == project.repository:
                raise ValueError("此仓库已有活跃项目")
        project.archived = False
        return self.db.save(project, "project_restored")

    def demo(self, planning):
        existing = next((p for p in self.db.list_projects() if p.is_demo), None)
        if existing:
            return existing
        p = Project(
            name="认证工作台", description="Vue + FastAPI · 注册与登录的演化示例", is_demo=True
        )
        definitions = [
            (
                "M01",
                "用户持久化契约",
                "建立用户实体、唯一约束与 SQLite 持久化。",
                ["backend/users/"],
                [],
                ["schema:users"],
                ["data"],
                "user.persistence",
                "用户记录可以持久化并保持标识唯一",
            ),
            (
                "M02",
                "凭据哈希与校验",
                "定义凭据哈希与失败校验的独立边界。",
                ["backend/security/"],
                [],
                ["security:password"],
                ["auth"],
                "auth.password",
                "密码以安全哈希存储并可校验",
            ),
            (
                "M03",
                "注册 API",
                "实现注册接口与输入校验，接入持久化和凭据服务。",
                ["backend/api/register.py"],
                ["M01", "M02"],
                ["api:auth"],
                ["api", "auth"],
                "auth.register",
                "合法注册请求创建真实用户，重复账号被拒绝",
            ),
            (
                "M04",
                "登录 API",
                "实现凭据认证与会话创建。",
                ["backend/api/login.py"],
                ["M01", "M02"],
                ["api:auth"],
                ["api", "auth"],
                "auth.login",
                "正确的用户名与密码可以创建会话",
            ),
            (
                "M05",
                "统一认证界面",
                "使用契约 mock 验证注册与登录切换及表单行为。",
                ["frontend/auth/"],
                [],
                ["ui:auth"],
                [],
                "ui.auth",
                "用户可以切换登录与注册表单并看到输入校验",
            ),
            (
                "M06",
                "认证状态管理",
                "通过 Pinia 集中管理会话及退出状态。",
                ["frontend/stores/auth.ts"],
                [],
                ["store:auth"],
                [],
                "auth.state",
                "登录与退出正确更新认证状态",
            ),
            (
                "M07",
                "真实端到端集成",
                "连通 GUI、状态管理与真实 API，验证完整用户路径。",
                ["tests/e2e/"],
                ["M03", "M04", "M05", "M06"],
                ["integration:auth"],
                ["auth"],
                "auth.e2e",
                "用户通过真实 GUI 注册、登录并退出",
            ),
        ]
        nodes = [
            {
                "id": mid,
                "title": title,
                "intent": intent,
                "scope": scope,
                "dependencies": deps,
                "dependency_reasons": {d: "集成需要该前置节点引入的契约或实现" for d in deps},
                "resources": resources,
                "change_types": kinds,
                "behaviors": [{"key": key, "statement": statement}],
            }
            for mid, title, intent, scope, deps, resources, kinds, key, statement in definitions
        ]
        p.proposal = PlanProposal(
            target="提供可持久化的注册、登录、会话管理和统一 Vue 界面。",
            summary="注册登录 V1 示例计划；所有任务均未执行，无真实验收证据。",
            milestones=nodes,
        )
        p.proposal_revision = 0
        created = self.db.create(p)
        if created.id != p.id:
            return created
        p = planning.apply(p.id, 0)
        for milestone in p.milestones:
            milestone.dependency_types = {
                dep: "verification" if milestone.id == "M07" else "implementation"
                for dep in milestone.dependencies
            }
        p = self.db.save(p, "example_initialized")
        self.db.message(
            p.id,
            "assistant",
            "这是一个未执行的注册登录示例。图中每个节点都是独立演化单元，连线只表示前置依赖。\n\n选择节点可查看行为断言和调查义务。连接真实仓库、配置 Provider 后，可以讨论目标或生成下一版计划。",
        )
        return p
