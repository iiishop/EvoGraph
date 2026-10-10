from contextlib import aclosing

from ..providers import adapters
from ..providers.base import check_url
from .secret_store_errors import secret_store_error_message


class SecretStore:
    """OS credential manager only. No plaintext fallback in SQLite or logs."""

    def get(self, name):
        errors = None
        try:
            import keyring
            from keyring import errors

            return keyring.get_password("EvoGraph", name) or ""
        except Exception as exc:
            raise ValueError(secret_store_error_message(exc, saving=False, errors=errors)) from None

    def set(self, name, secret):
        errors = None
        try:
            import keyring
            from keyring import errors

            keyring.set_password("EvoGraph", name, secret)
        except Exception as exc:
            raise ValueError(secret_store_error_message(exc, saving=True, errors=errors)) from None


class SettingsService:
    def __init__(self, db, secrets=None):
        self.db = db
        self.secrets = secrets or SecretStore()

    def get(self):
        return {
            "adapters": [a.descriptor for a in adapters().values()],
            "provider": self.db.setting("provider", None),
        }

    def save(self, adapter: str, config: dict, api_key: str = "", clear_key: bool = False):
        if adapter not in adapters():
            raise ValueError("不支持的 Provider")
        descriptor = adapters()[adapter].descriptor
        clean = {}
        for field in descriptor["fields"]:
            value = str(config.get(field["key"], "")).strip()
            if field.get("required") and not value:
                raise ValueError(f"请填写 {field['label']}")
            clean[field["key"]] = value
        check_url(clean["base_url"])
        # Credentials are scoped to endpoint, so changing URL never leaks the old key.
        import hashlib

        secret_id = hashlib.sha256(
            (adapter + ":" + clean["base_url"].rstrip("/")).encode()
        ).hexdigest()
        old = self.db.setting("provider", {})
        has_key = old.get("has_key", False) if old.get("secret_id") == secret_id else False
        if api_key or clear_key:
            self.secrets.set(secret_id, api_key if not clear_key else "")
            has_key = bool(api_key) and not clear_key
        self.db.set_setting(
            "provider",
            {"adapter": adapter, "config": clean, "secret_id": secret_id, "has_key": has_key},
        )
        return self.get()

    async def complete(self, messages):
        saved = self.db.setting("provider")
        if not saved:
            raise ValueError("请先在设置中配置 Provider，或使用示例项目查看界面")
        secret = self.secrets.get(saved["secret_id"]) if saved.get("has_key") else ""
        return await adapters()[saved["adapter"]].complete(saved["config"], secret, messages)

    async def test(self):
        text, _ = await self.complete([{"role": "user", "content": "Reply with OK only."}])
        return {"ok": True, "message": text[:200]}

    async def stream(self, messages, tools, *, request_controls=None):
        saved = self.db.setting("provider")
        if not saved:
            raise ValueError("请先在设置中配置支持工具调用的 Provider")
        adapter = adapters()[saved["adapter"]]
        if not hasattr(adapter, "stream"):
            raise ValueError("当前 Provider 适配器不支持流式工具调用")
        options = {}
        if request_controls is not None:
            if saved["adapter"] != "openai_compatible":
                raise ValueError("当前 Provider 不支持评审 low 实验，未发送请求")
            # Validate before credential access, without changing saved config.
            options["request_controls"] = adapter.validate_request_controls(
                saved["config"], request_controls)
        secret = self.secrets.get(saved["secret_id"]) if saved.get("has_key") else ""
        async with aclosing(adapter.stream(saved["config"], secret, messages, tools, **options)) as stream:
            async for event in stream:
                yield event
