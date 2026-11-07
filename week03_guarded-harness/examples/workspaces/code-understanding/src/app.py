def login_status(is_authenticated: bool) -> str:
    return "ok" if is_authenticated else "denied"
