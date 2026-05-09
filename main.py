from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from common.auth.config import JWT_SECRET
from common.errors.errors import register_error_handlers
from routes.accounts import router as accounts_router
from routes.auth import router as auth_router
from routes.budget_items import flat_router as budget_items_flat_router
from routes.budget_items import router as budget_items_router
from routes.budgets import router as budgets_router
from routes.categories import router as categories_router
from routes.dashboard import router as dashboard_router
from routes.recurring import router as recurring_router
from routes.seed import router as seed_router
from routes.transaction_kinds import router as transaction_kinds_router
from routes.transactions import router as transactions_router
from routes.users import router as user_router
from routes.views import router as views_router

app = FastAPI(title="Coin Purse")
register_error_handlers(app)
app.add_middleware(SessionMiddleware, secret_key=JWT_SECRET)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)

app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(budgets_router)
app.include_router(budget_items_router)
app.include_router(budget_items_flat_router)
app.include_router(categories_router)
app.include_router(dashboard_router)
app.include_router(recurring_router)
app.include_router(seed_router)
app.include_router(transaction_kinds_router)
app.include_router(transactions_router)
app.include_router(user_router)
app.include_router(views_router)

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
