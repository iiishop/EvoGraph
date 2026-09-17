from backend.storage.users import find_user
def login(email): return find_user(email)