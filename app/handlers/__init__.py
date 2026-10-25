from aiogram import Router

from app.handlers.fallback import router as fallback_router
from app.handlers.save import router as save_router
from app.handlers.search import router as search_router
from app.handlers.settings import router as settings_router
from app.handlers.start import router as start_router
from app.handlers.today import router as today_router
from app.handlers.vault import router as vault_router


def build_router() -> Router:
    root = Router(name="root")
    root.include_router(start_router)
    root.include_router(vault_router)
    root.include_router(search_router)
    root.include_router(settings_router)
    root.include_router(today_router)
    root.include_router(save_router)
    root.include_router(fallback_router)
    return root
