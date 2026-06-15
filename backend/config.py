"""
Konfiguráció — környezeti változók SZIGORÚ beolvasása. NINCS FALLBACK.

A kötelező változók (DATABASE_URL, API_KEY_ENCRYPTION_KEY) hiánya azonnali
RuntimeError-t okoz. Tilos `os.environ.get(X) or default` típusú csendes
helyettesítés — minden hiányzó kötelező változó hibát ad.
"""
import os
from pathlib import Path


# --- Anthropic Admin API: állandó végpont és verzió ------------------------
# Ezek nem környezeti változók és nem fallback-ek: a hivatalos, rögzített
# API-cím és verzió. Az Admin API kulcs (sk-ant-admin...) NEM env-ből jön,
# hanem a felületen, Fernet-titkosítva a `admin_api_keys` táblában él.
ANTHROPIC_API_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def get_version() -> str:
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


APP_VERSION = get_version()
USER_AGENT = f"AnthropicManager/{APP_VERSION}"


def require_env(name: str) -> str:
    """Kötelező környezeti változó beolvasása. Hiány/üres → RuntimeError.

    NO FALLBACK: az alkalmazás induljon hibára, ha a változó nem olvasható.
    """
    val = os.environ.get(name)
    if val is None or val == "":
        raise RuntimeError(
            f"A(z) {name} környezeti változó kötelező, de nincs beállítva. "
            f"Lásd .env.example / docs/installation.md."
        )
    return val


def get_database_url() -> str:
    return require_env("DATABASE_URL")


def get_encryption_key() -> str:
    return require_env("API_KEY_ENCRYPTION_KEY")


# --- Környezet-azonosító badge (opcionális, kozmetikai) --------------------
# A referenciaprojekt mintája szerint: üres érték = "ne jelenjen meg badge".
# Ez nem fallback egy hiányzó kötelező változóra, hanem egy explicit opcionális
# funkció, ahol az üres string maga a "kikapcsolva" jelentés.
ENV_TYPE = os.environ.get("ENV_TYPE", "")
ENV_COLOR = os.environ.get("ENV_COLOR", "")
