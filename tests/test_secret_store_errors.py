"""Synthetic stdlib checks: python tests/test_secret_store_errors.py.

No application/provider imports, installed keyring, or real credential operations.
"""

import ast
import builtins
import importlib.util
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace

APPLICATION = Path(__file__).resolve().parents[1] / "backend/evograph/application"
SPEC = importlib.util.spec_from_file_location(
    "synthetic_secret_store_errors", APPLICATION / "secret_store_errors.py"
)
MESSAGES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MESSAGES)
ERRORS = SimpleNamespace(**{
    name: type(name, (Exception,), {})
    for name in ("NoKeyringError", "KeyringLocked", "InitError")
})
CANARY = "fabricated-error-body-do-not-display"
REASONS = {
    "missing": "未找到 keyring 依赖，请检查 EvoGraph 运行环境中的项目依赖",
    "NoKeyringError": "未找到可用的凭据库后端，请检查 keyring 与系统凭据服务的集成",
    "KeyringLocked": "系统凭据库未能解锁，请在系统凭据管理器中解锁后重试",
    "InitError": "系统凭据库初始化失败，请检查系统凭据服务后重试",
}


def prefix(saving):
    return "无法将密钥保存到系统凭据库" if saving else "无法从系统凭据库读取密钥"


def isolated_classes(keyring=None, import_failure=None):
    """Execute only source class definitions with a local, fake-only importer."""
    def synthetic_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name != "keyring" or level:
            raise AssertionError("Unexpected import in synthetic credential wrapper")
        if import_failure is not None:
            raise import_failure
        return keyring

    source = ast.parse((APPLICATION / "settings.py").read_text())
    classes = ast.Module(
        body=[node for node in source.body
              if isinstance(node, ast.ClassDef) and node.name == "SecretStore"],
        type_ignores=[],
    )
    namespace = {
        "__builtins__": {**vars(builtins), "__import__": synthetic_import},
        "secret_store_error_message": MESSAGES.secret_store_error_message,
    }
    exec(compile(classes, "synthetic_settings_classes", "exec"), namespace)
    return namespace


class SecretStoreErrorTests(unittest.TestCase):
    def test_known_conditions_have_exact_read_and_save_messages(self):
        failures = {name: getattr(ERRORS, name)(CANARY) for name in vars(ERRORS)}
        failures["missing"] = ModuleNotFoundError(CANARY, name="keyring")
        for name, exc in failures.items():
            for saving in (False, True):
                with self.subTest(condition=name, saving=saving):
                    message = MESSAGES.secret_store_error_message(exc, saving=saving, errors=ERRORS)
                    self.assertEqual(message, prefix(saving) + "：" + REASONS[name])
                    self.assertNotIn(CANARY, message)

    def test_unknown_conditions_stay_generic(self):
        failures = [
            RuntimeError("Qt6 QCA plugin missing; locked; " + CANARY),
            ImportError(CANARY, name="keyring"),
            ModuleNotFoundError(CANARY, name="unrelated_dependency"),
            ModuleNotFoundError("No module named keyring; " + CANARY),
            PermissionError(CANARY),
            type("KeyringLocked", (Exception,), {})(CANARY),
            type("PasswordSetError", (Exception,), {})(CANARY),
        ]
        for exc in failures:
            for saving in (False, True):
                with self.subTest(error_type=type(exc).__name__, saving=saving):
                    self.assertEqual(
                        MESSAGES.secret_store_error_message(exc, saving=saving, errors=ERRORS),
                        prefix(saving),
                    )

    def test_exception_body_and_chain_are_never_formatted(self):
        class Unprintable(ERRORS.KeyringLocked):
            def __str__(self):
                raise AssertionError("Must not stringify exception")

            def __repr__(self):
                raise AssertionError("Must not represent exception")

        exc = Unprintable(CANARY)
        exc.__cause__ = RuntimeError(CANARY)
        exc.add_note(CANARY)
        for saving in (False, True):
            self.assertEqual(
                MESSAGES.secret_store_error_message(exc, saving=saving, errors=ERRORS),
                prefix(saving) + "：" + REASONS["KeyringLocked"],
            )

    def test_import_failures_do_not_require_keyring_errors(self):
        for saving in (False, True):
            self.assertEqual(
                MESSAGES.secret_store_error_message(
                    ModuleNotFoundError(CANARY, name="keyring"), saving=saving
                ),
                prefix(saving) + "：" + REASONS["missing"],
            )
            self.assertEqual(
                MESSAGES.secret_store_error_message(RuntimeError(CANARY), saving=saving),
                prefix(saving),
            )

    def test_fake_wrappers_preserve_return_values_and_arguments(self):
        calls = []
        fake = SimpleNamespace(errors=ERRORS)
        store = isolated_classes(fake)["SecretStore"]()
        for value in (None, "", "fabricated-read-value"):
            def read(*args):
                calls.append(args)
                return value
            fake.get_password = read
            self.assertEqual(store.get("fabricated-name"), value or "")
            self.assertEqual(calls[-1], ("EvoGraph", "fabricated-name"))
        fake.set_password = lambda *args: calls.append(args)
        for value in ("fabricated-write-value", ""):
            self.assertIsNone(store.set("fabricated-name", value))
            self.assertEqual(calls[-1], ("EvoGraph", "fabricated-name", value))

    def test_fake_wrapper_failures_are_safe_value_errors(self):
        for saving in (False, True):
            for condition in (*vars(ERRORS), "missing", "unknown"):
                exc = (getattr(ERRORS, condition)(CANARY) if condition in vars(ERRORS)
                       else ModuleNotFoundError(CANARY, name="keyring") if condition == "missing"
                       else RuntimeError(CANARY))
                exc.add_note(CANARY)
                def fail(*args):
                    raise exc
                fake = SimpleNamespace(errors=ERRORS, get_password=fail, set_password=fail)
                store = isolated_classes(
                    fake, import_failure=exc if condition == "missing" else None
                )["SecretStore"]()
                with self.subTest(saving=saving, condition=condition):
                    try:
                        store.set("fabricated-name", "fabricated-value") if saving else store.get(
                            "fabricated-name"
                        )
                    except ValueError as public:
                        expected = prefix(saving)
                        if condition != "unknown":
                            expected += "：" + REASONS[condition]
                        self.assertEqual(str(public), expected)
                        self.assertIsNone(public.__cause__)
                        self.assertTrue(public.__suppress_context__)
                        self.assertNotIn(CANARY, "".join(traceback.format_exception(public)))
                    else:
                        self.fail("Credential wrapper did not raise ValueError")

    def test_secret_write_precedes_config_write_source_contract(self):
        # A source contract protects save-before-config ordering and clear-key arguments
        # without executing settings, URL validation, provider code, or real stores.
        source = ast.parse((APPLICATION / "settings.py").read_text())
        service = next(node for node in source.body
                       if isinstance(node, ast.ClassDef) and node.name == "SettingsService")
        save = next(node for node in service.body if getattr(node, "name", None) == "save")
        secret_write = next(node for node in ast.walk(save)
                            if isinstance(node, ast.Call)
                            and ast.unparse(node.func) == "self.secrets.set")
        config_write = next(node for node in ast.walk(save)
                            if isinstance(node, ast.Call)
                            and ast.unparse(node.func) == "self.db.set_setting")
        self.assertLess(secret_write.lineno, config_write.lineno)
        self.assertEqual(ast.unparse(secret_write.args[0]), "secret_id")
        self.assertEqual(ast.unparse(secret_write.args[1]), "api_key if not clear_key else ''")


if __name__ == "__main__":
    unittest.main()
