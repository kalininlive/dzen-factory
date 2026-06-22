import json
from pathlib import Path


def load_cookies(path: str) -> list:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return []
    with open(p, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    
    normalized = []
    for c in cookies:
        norm = _normalize(c)
        normalized.append(norm)
        
        # Автоматическое дублирование кук Яндекса между доменами ya.ru и yandex.ru
        # Это решает проблему несовпадения доменов при проверке сессии Дзена
        domain = norm.get("domain", "")
        if "ya.ru" in domain:
            c_yandex = norm.copy()
            c_yandex["domain"] = domain.replace("ya.ru", "yandex.ru")
            normalized.append(c_yandex)
        elif "yandex.ru" in domain:
            c_ya = norm.copy()
            c_ya["domain"] = domain.replace("yandex.ru", "ya.ru")
            normalized.append(c_ya)
            
    return normalized


def _normalize(c: dict) -> dict:
    """Приводит cookie из формата Cookie-Editor к формату Playwright."""
    result = {
        "name": c.get("name", ""),
        "value": c.get("value", ""),
        "domain": c.get("domain", ""),
        "path": c.get("path", "/"),
        "httpOnly": c.get("httpOnly", False),
        "secure": c.get("secure", False),
    }
    # Cookie-Editor использует expirationDate, Playwright — expires
    exp = c.get("expires") or c.get("expirationDate")
    if exp is not None:
        result["expires"] = float(exp)
    # Нормализуем sameSite
    same_site = c.get("sameSite", "")
    if same_site in ("Strict", "Lax", "None"):
        result["sameSite"] = same_site
    return result


def save_cookies(path: str, cookies: list) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)


def cookies_exist(path: str) -> bool:
    p = Path(path)
    return p.exists() and p.stat().st_size > 100
