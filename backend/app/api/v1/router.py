from fastapi import APIRouter

from app.api.v1 import auth, catalog, erp, inventory, locations, receivings, stores, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(stores.router)
api_router.include_router(users.router)
api_router.include_router(locations.router)
api_router.include_router(catalog.router)
api_router.include_router(erp.router)
api_router.include_router(inventory.router)
api_router.include_router(receivings.router)
