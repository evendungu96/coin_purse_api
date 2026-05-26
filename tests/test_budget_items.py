"""Tests for budget items endpoints (nested + flat)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

BUDGET_PAYLOAD = {
    "name": "April 2026",
    "period_start": "2026-04-01",
    "period_end": "2026-04-30",
}


@pytest.fixture
def budget(client: TestClient, user_id: str) -> dict:
    res = client.post(f"/users/{user_id}/budgets", json=BUDGET_PAYLOAD)
    assert res.status_code == 201
    return res.json()


@pytest.fixture
def category(client: TestClient, user_id: str) -> dict:
    res = client.post(f"/users/{user_id}/categories", json={"name": "Groceries"})
    assert res.status_code == 201
    return res.json()


@pytest.fixture
def budget_item(client: TestClient, user_id: str, budget: dict, category: dict) -> dict:
    res = client.post(
        f"/users/{user_id}/budgets/{budget['id']}/items",
        json={"category_id": category["id"], "limit_amount": "500.00"},
    )
    assert res.status_code == 201
    return res.json()


# ---------------------------------------------------------------------------
# POST /users/{user_id}/budgets/{budget_id}/items
# ---------------------------------------------------------------------------


def test_create_budget_item_returns_201(
    client: TestClient, user_id: str, budget: dict, category: dict
):
    res = client.post(
        f"/users/{user_id}/budgets/{budget['id']}/items",
        json={"category_id": category["id"], "limit_amount": "500.00"},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["budget_id"] == budget["id"]
    assert data["budget_name"] == budget["name"]
    assert data["category_id"] == category["id"]
    assert data["category_name"] == category["name"]
    assert data["limit_amount"] == "500.00"
    assert "id" in data


def test_create_budget_item_duplicate_category_allowed(
    client: TestClient, user_id: str, budget: dict, category: dict
):
    client.post(
        f"/users/{user_id}/budgets/{budget['id']}/items",
        json={"category_id": category["id"], "limit_amount": "100.00"},
    )
    res = client.post(
        f"/users/{user_id}/budgets/{budget['id']}/items",
        json={"category_id": category["id"], "limit_amount": "200.00"},
    )
    assert res.status_code == 201


def test_create_budget_item_unknown_budget_returns_404(
    client: TestClient, user_id: str, category: dict
):
    res = client.post(
        f"/users/{user_id}/budgets/{uuid.uuid4()}/items",
        json={"category_id": category["id"], "limit_amount": "100.00"},
    )
    assert res.status_code == 404


def test_create_budget_item_unknown_category_returns_404(
    client: TestClient, user_id: str, budget: dict
):
    res = client.post(
        f"/users/{user_id}/budgets/{budget['id']}/items",
        json={"category_id": str(uuid.uuid4()), "limit_amount": "100.00"},
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# GET /users/{user_id}/budgets/{budget_id}/items
# ---------------------------------------------------------------------------


def test_list_budget_items(
    client: TestClient, user_id: str, budget: dict, budget_item: dict
):
    res = client.get(f"/users/{user_id}/budgets/{budget['id']}/items")
    assert res.status_code == 200
    ids = [i["id"] for i in res.json()]
    assert budget_item["id"] in ids


def test_list_budget_items_empty(client: TestClient, user_id: str, budget: dict):
    res = client.get(f"/users/{user_id}/budgets/{budget['id']}/items")
    assert res.status_code == 200
    assert res.json() == []


# ---------------------------------------------------------------------------
# GET /users/{user_id}/budgets/{budget_id}/items/{item_id}
# ---------------------------------------------------------------------------


def test_get_budget_item(
    client: TestClient, user_id: str, budget: dict, budget_item: dict
):
    res = client.get(
        f"/users/{user_id}/budgets/{budget['id']}/items/{budget_item['id']}"
    )
    assert res.status_code == 200
    assert res.json()["id"] == budget_item["id"]


def test_get_budget_item_not_found_returns_404(
    client: TestClient, user_id: str, budget: dict
):
    res = client.get(f"/users/{user_id}/budgets/{budget['id']}/items/{uuid.uuid4()}")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /users/{user_id}/budgets/{budget_id}/items/{item_id}
# ---------------------------------------------------------------------------


def test_update_budget_item_limit(
    client: TestClient, user_id: str, budget: dict, budget_item: dict
):
    res = client.patch(
        f"/users/{user_id}/budgets/{budget['id']}/items/{budget_item['id']}",
        json={"limit_amount": "999.00"},
    )
    assert res.status_code == 200
    assert res.json()["limit_amount"] == "999.00"


# ---------------------------------------------------------------------------
# DELETE /users/{user_id}/budgets/{budget_id}/items/{item_id}
# ---------------------------------------------------------------------------


def test_delete_budget_item_returns_204(
    client: TestClient, user_id: str, budget: dict, budget_item: dict
):
    res = client.delete(
        f"/users/{user_id}/budgets/{budget['id']}/items/{budget_item['id']}"
    )
    assert res.status_code == 204


def test_deleted_budget_item_not_in_list(
    client: TestClient, user_id: str, budget: dict, budget_item: dict
):
    client.delete(f"/users/{user_id}/budgets/{budget['id']}/items/{budget_item['id']}")
    ids = [
        i["id"]
        for i in client.get(f"/users/{user_id}/budgets/{budget['id']}/items").json()
    ]
    assert budget_item["id"] not in ids


# ---------------------------------------------------------------------------
# Flat routes — GET /users/{user_id}/budget-items
# ---------------------------------------------------------------------------


def test_flat_list_all_items(client: TestClient, user_id: str, budget_item: dict):
    res = client.get(f"/users/{user_id}/budget-items")
    assert res.status_code == 200
    ids = [i["id"] for i in res.json()]
    assert budget_item["id"] in ids


def test_flat_list_filter_by_budget_id(
    client: TestClient, user_id: str, budget: dict, budget_item: dict
):  # noqa: ARG001
    res = client.get(f"/users/{user_id}/budget-items?budget_id={budget['id']}")
    assert res.status_code == 200
    assert all(i["budget_id"] == budget["id"] for i in res.json())


def test_flat_list_filter_wrong_budget_id_returns_empty(
    client: TestClient, user_id: str, budget_item: dict
):  # noqa: ARG001
    res = client.get(f"/users/{user_id}/budget-items?budget_id={uuid.uuid4()}")
    assert res.status_code == 200
    assert res.json() == []


def test_flat_get_budget_item(client: TestClient, user_id: str, budget_item: dict):
    res = client.get(f"/users/{user_id}/budget-items/{budget_item['id']}")
    assert res.status_code == 200
    assert res.json()["id"] == budget_item["id"]


def test_flat_get_wrong_user_returns_404(client: TestClient, budget_item: dict):
    res = client.get(f"/users/{uuid.uuid4()}/budget-items/{budget_item['id']}")
    assert res.status_code == 404


def test_flat_update_budget_item(client: TestClient, user_id: str, budget_item: dict):
    res = client.patch(
        f"/users/{user_id}/budget-items/{budget_item['id']}",
        json={"limit_amount": "750.00"},
    )
    assert res.status_code == 200
    assert res.json()["limit_amount"] == "750.00"


def test_flat_delete_budget_item(client: TestClient, user_id: str, budget_item: dict):
    res = client.delete(f"/users/{user_id}/budget-items/{budget_item['id']}")
    assert res.status_code == 204
