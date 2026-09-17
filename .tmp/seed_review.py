from pathlib import Path
from evograph.application.api import Application
from evograph.domain.models import ArchitectureSpec, ProposedMilestone

root = Path('.tmp/review_repo').resolve()
for name, text in {
    'frontend/views/Login.vue': '<script>import {login} from "../api/auth";</script>',
    'frontend/api/auth.ts': 'export function login() { return fetch("/api/login"); }',
    'backend/api/auth.py': 'from backend.storage.users import find_user\ndef login(email): return find_user(email)',
    'backend/storage/users.py': 'def find_user(email): return {"email": email}',
    'tests/test_auth.py': 'def test_login_contract():\n    assert 1 == 1\n',
}.items():
    path=root/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text,encoding='utf-8')
app=Application(Path('.evograph/review-v2'))
p=app.projects.create('身份与访问工作台', '以源码为依据，逐步演化登录与权限体验', str(root), request_id='review-v2')
app.execution.refresh(p.id)
if not app.db.get(p.id).architectures:
    app.design.update(p.id,ArchitectureSpec.model_validate({
      'summary':'浏览器通过认证服务访问用户数据；登录令牌由安全边界统一管理。',
      'technologies':[{'area':'交互','choice':'Vue 3','rationale':'复用现有登录视图与组件边界。'},{'area':'持久化','choice':'SQLite','rationale':'单机部署，以事务维护用户状态。'}],
      'decisions':['浏览器不能直接访问持久化层。','令牌验证集中在认证服务，避免重复实现。'],
      'diagram':{'id':'identity','title':'身份系统','nodes':[
        {'id':'ui','label':'登录工作台','description':'邮箱登录、表单校验与会话状态','role':'frontend','source_refs':['frontend/views/Login.vue']},
        {'id':'auth','label':'认证服务','description':'登录请求、凭据验证与用户查询','role':'backend','source_refs':['backend/api/auth.py']},
        {'id':'tokens','label':'令牌边界','description':'签发、过期与撤销策略','role':'security'},
        {'id':'db','label':'用户存储','description':'账号资料与索引','role':'database','source_refs':['backend/storage/users.py']}],
      'edges':[{'source':'ui','target':'auth','label':'HTTPS / 登录请求'},{'source':'auth','target':'db','label':'查询账号'},{'source':'auth','target':'tokens','label':'签发会话'},{'source':'tokens','target':'auth','label':'验证凭据'}]}}))
    for mid,title,scope,comp,deps in [('M01','邮箱登录与错误提示','frontend/views/Login.vue','ui',[]),('M02','安全会话与退出','backend/api/auth.py','auth',['M01'])]:
        app.graph.upsert(p.id,ProposedMilestone(id=mid,title=title,intent='交付可独立合并的身份验证能力',scope=[scope],architecture_components=[comp],dependencies=deps,dependency_reasons={d:'复用已稳定的登录协议' for d in deps},behaviors=[{'key':mid,'statement':title+'符合定义的交互契约'}]),True)
    app.graph.finalize(p.id)
print(p.id)
