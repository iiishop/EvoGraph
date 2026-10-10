"""Fixed credential-store messages, without backend probing or exception text."""


def secret_store_error_message(exc, *, saving, errors=None):
    """`errors` is keyring.errors, supplied only after keyring imports successfully.

    Contracts: https://github.com/jaraco/keyring/blob/v25.7.0/keyring/errors.py
    Never inspect exception strings, arguments, chains, or backend configuration.
    """
    message = "无法将密钥保存到系统凭据库" if saving else "无法从系统凭据库读取密钥"
    if isinstance(exc, ModuleNotFoundError) and exc.name == "keyring":
        reason = "未找到 keyring 依赖，请检查 EvoGraph 运行环境中的项目依赖"
    elif errors is not None and isinstance(exc, errors.NoKeyringError):
        reason = "未找到可用的凭据库后端，请检查 keyring 与系统凭据服务的集成"
    elif errors is not None and isinstance(exc, errors.KeyringLocked):
        reason = "系统凭据库未能解锁，请在系统凭据管理器中解锁后重试"
    elif errors is not None and isinstance(exc, errors.InitError):
        reason = "系统凭据库初始化失败，请检查系统凭据服务后重试"
    else:
        return message
    return f"{message}：{reason}"
