"""Small real source file used by the local UI integration fixture."""


def login(email: str) -> bool:
    return "@" in email
