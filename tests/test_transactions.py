"""Tests for /users/{user_id}/transactions endpoints."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def account(client: TestClient, user_id: str) -> dict:
    res = client.post(
        f"/users/{user_id}/accounts", json={"name": "Main", "account_type": "bank"}
    )
    assert res.status_code == 201
    return res.json()


@pytest.fixture
def second_account(client: TestClient, user_id: str) -> dict:
    res = client.post(
        f"/users/{user_id}/accounts", json={"name": "Savings", "account_type": "bank"}
    )
    assert res.status_code == 201
    return res.json()


@pytest.fixture
def category(client: TestClient, user_id: str) -> dict:
    res = client.post(f"/users/{user_id}/categories", json={"name": "Food"})
    assert res.status_code == 201
    return res.json()


def _income(account_id: str, **kwargs) -> dict:
    return {
        "kind": "income",
        "account_id": account_id,
        "amount": "100.00",
        "posted_at": "2026-04-01",
        **kwargs,
    }


def _expense(account_id: str, **kwargs) -> dict:
    return {
        "kind": "expense",
        "account_id": account_id,
        "amount": "50.00",
        "posted_at": "2026-04-02",
        **kwargs,
    }


def _transfer(account_id: str, to_account_id: str, **kwargs) -> dict:
    return {
        "kind": "transfer",
        "account_id": account_id,
        "to_account_id": to_account_id,
        "amount": "200.00",
        "posted_at": "2026-04-03",
        **kwargs,
    }


# ---------------------------------------------------------------------------
# POST /users/{user_id}/transactions
# ---------------------------------------------------------------------------


def test_create_income_returns_201(client: TestClient, user_id: str, account: dict):
    res = client.post(f"/users/{user_id}/transactions", json=_income(account["id"]))
    assert res.status_code == 201
    data = res.json()
    assert data["kind"] == "income"
    assert data["account_id"] == account["id"]
    assert data["account_name"] == account["name"]
    assert data["amount"] == "100.00"
    assert data["category_name"] is None
    assert data["to_account_name"] is None


def test_create_expense_with_category(
    client: TestClient, user_id: str, account: dict, category: dict
):
    res = client.post(
        f"/users/{user_id}/transactions",
        json=_expense(account["id"], category_id=category["id"]),
    )
    assert res.status_code == 201
    data = res.json()
    assert data["kind"] == "expense"
    assert data["category_id"] == category["id"]
    assert data["category_name"] == category["name"]


def test_create_transfer_returns_201(
    client: TestClient, user_id: str, account: dict, second_account: dict
):
    res = client.post(
        f"/users/{user_id}/transactions",
        json=_transfer(account["id"], second_account["id"]),
    )
    assert res.status_code == 201
    data = res.json()
    assert data["kind"] == "transfer"
    assert data["to_account_id"] == second_account["id"]
    assert data["to_account_name"] == second_account["name"]


def test_create_transfer_missing_to_account_returns_422(
    client: TestClient, user_id: str, account: dict
):
    res = client.post(
        f"/users/{user_id}/transactions",
        json={
            "kind": "transfer",
            "account_id": account["id"],
            "amount": "10.00",
            "posted_at": "2026-04-01",
        },
    )
    assert res.status_code == 422


def test_create_transfer_same_account_returns_422(
    client: TestClient, user_id: str, account: dict
):
    res = client.post(
        f"/users/{user_id}/transactions",
        json=_transfer(account["id"], account["id"]),
    )
    assert res.status_code == 422


def test_create_income_with_to_account_returns_422(
    client: TestClient, user_id: str, account: dict, second_account: dict
):
    res = client.post(
        f"/users/{user_id}/transactions",
        json=_income(account["id"], to_account_id=second_account["id"]),
    )
    assert res.status_code == 422


def test_create_refund_requires_refunded_id(
    client: TestClient, user_id: str, account: dict
):
    res = client.post(
        f"/users/{user_id}/transactions",
        json={
            "kind": "refund",
            "account_id": account["id"],
            "amount": "10.00",
            "posted_at": "2026-04-01",
        },
    )
    assert res.status_code == 422


def test_create_refund_links_original(client: TestClient, user_id: str, account: dict):
    original = client.post(
        f"/users/{user_id}/transactions", json=_expense(account["id"])
    ).json()
    res = client.post(
        f"/users/{user_id}/transactions",
        json={
            "kind": "refund",
            "account_id": account["id"],
            "amount": "10.00",
            "posted_at": "2026-04-05",
            "refunded_transaction_id": original["id"],
        },
    )
    assert res.status_code == 201
    assert res.json()["refunded_transaction_id"] == original["id"]


def test_create_transaction_unknown_account_returns_404(
    client: TestClient, user_id: str
):
    res = client.post(
        f"/users/{user_id}/transactions",
        json=_income(str(uuid.uuid4())),
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# GET /users/{user_id}/transactions
# ---------------------------------------------------------------------------


def test_list_transactions_empty(client: TestClient, user_id: str):
    res = client.get(f"/users/{user_id}/transactions")
    assert res.status_code == 200
    assert res.json() == []


def test_list_transactions_returns_own(client: TestClient, user_id: str, account: dict):
    client.post(f"/users/{user_id}/transactions", json=_income(account["id"]))
    client.post(f"/users/{user_id}/transactions", json=_expense(account["id"]))
    res = client.get(f"/users/{user_id}/transactions")
    assert len(res.json()) == 2


def test_list_transactions_filter_by_account(
    client: TestClient, user_id: str, account: dict, second_account: dict
):
    client.post(f"/users/{user_id}/transactions", json=_income(account["id"]))
    client.post(f"/users/{user_id}/transactions", json=_income(second_account["id"]))
    res = client.get(f"/users/{user_id}/transactions?account_id={account['id']}")
    assert all(t["account_id"] == account["id"] for t in res.json())


def test_list_transactions_filter_by_date_range(
    client: TestClient, user_id: str, account: dict
):
    client.post(
        f"/users/{user_id}/transactions",
        json=_income(account["id"], posted_at="2026-03-01"),
    )
    client.post(
        f"/users/{user_id}/transactions",
        json=_income(account["id"], posted_at="2026-04-15"),
    )
    res = client.get(f"/users/{user_id}/transactions?start=2026-04-01&end=2026-04-30")
    dates = [t["posted_at"] for t in res.json()]
    assert all(d >= "2026-04-01" for d in dates)
    assert "2026-03-01" not in dates


def test_list_transactions_filter_by_category(
    client: TestClient, user_id: str, account: dict, category: dict
):
    client.post(
        f"/users/{user_id}/transactions",
        json=_expense(account["id"], category_id=category["id"]),
    )
    client.post(f"/users/{user_id}/transactions", json=_expense(account["id"]))
    res = client.get(f"/users/{user_id}/transactions?category_id={category['id']}")
    assert all(t["category_id"] == category["id"] for t in res.json())


def test_list_transactions_filter_by_kind(
    client: TestClient, user_id: str, account: dict, second_account: dict
):
    client.post(f"/users/{user_id}/transactions", json=_income(account["id"]))
    client.post(f"/users/{user_id}/transactions", json=_expense(account["id"]))
    client.post(
        f"/users/{user_id}/transactions",
        json=_transfer(account["id"], second_account["id"]),
    )
    res = client.get(f"/users/{user_id}/transactions?kind=income")
    assert res.status_code == 200
    assert len(res.json()) == 1
    assert res.json()[0]["kind"] == "income"


def test_list_transactions_response_includes_ids_and_names(
    client: TestClient, user_id: str, account: dict, category: dict
):
    client.post(
        f"/users/{user_id}/transactions",
        json=_expense(account["id"], category_id=category["id"]),
    )
    txn = client.get(f"/users/{user_id}/transactions").json()[0]
    assert txn["account_id"] == account["id"]
    assert txn["account_name"] == account["name"]
    assert txn["category_id"] == category["id"]
    assert txn["category_name"] == category["name"]
    assert txn["kind"] == "expense"
    assert "id" in txn


# ---------------------------------------------------------------------------
# GET /users/{user_id}/transactions/{transaction_id}
# ---------------------------------------------------------------------------


def test_get_transaction(client: TestClient, user_id: str, account: dict):
    created = client.post(
        f"/users/{user_id}/transactions", json=_income(account["id"])
    ).json()
    res = client.get(f"/users/{user_id}/transactions/{created['id']}")
    assert res.status_code == 200
    assert res.json()["id"] == created["id"]


def test_get_transaction_wrong_user_returns_404(
    client: TestClient, user_id: str, account: dict
):
    created = client.post(
        f"/users/{user_id}/transactions", json=_income(account["id"])
    ).json()
    res = client.get(f"/users/{uuid.uuid4()}/transactions/{created['id']}")
    assert res.status_code == 404


def test_get_transaction_not_found_returns_404(client: TestClient, user_id: str):
    res = client.get(f"/users/{user_id}/transactions/{uuid.uuid4()}")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /users/{user_id}/transactions/{transaction_id}
# ---------------------------------------------------------------------------


def test_update_transaction_description(
    client: TestClient, user_id: str, account: dict
):
    created = client.post(
        f"/users/{user_id}/transactions", json=_income(account["id"], description="Old")
    ).json()
    res = client.patch(
        f"/users/{user_id}/transactions/{created['id']}",
        json={"description": "New"},
    )
    assert res.status_code == 200
    assert res.json()["description"] == "New"


def test_update_transaction_wrong_user_returns_404(
    client: TestClient, user_id: str, account: dict
):
    created = client.post(
        f"/users/{user_id}/transactions", json=_income(account["id"])
    ).json()
    res = client.patch(
        f"/users/{uuid.uuid4()}/transactions/{created['id']}", json={"description": "x"}
    )
    assert res.status_code == 404


def test_update_transfer_description_and_amount(
    client: TestClient, user_id: str, account: dict, second_account: dict
):
    transfer = client.post(
        f"/users/{user_id}/transactions",
        json=_transfer(account["id"], second_account["id"]),
    ).json()
    res = client.patch(
        f"/users/{user_id}/transactions/{transfer['id']}",
        json={"description": "changed", "amount": "50.00"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["description"] == "changed"
    assert float(data["amount"]) == 50.00


def test_update_transfer_accounts(
    client: TestClient, user_id: str, account: dict, second_account: dict
):
    transfer = client.post(
        f"/users/{user_id}/transactions",
        json=_transfer(account["id"], second_account["id"]),
    ).json()
    # Swap from/to accounts
    res = client.patch(
        f"/users/{user_id}/transactions/{transfer['id']}",
        json={"account_id": second_account["id"], "to_account_id": account["id"]},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["account_id"] == second_account["id"]
    assert data["to_account_id"] == account["id"]


def test_update_non_transfer_still_works(
    client: TestClient, user_id: str, account: dict
):
    txn = client.post(
        f"/users/{user_id}/transactions",
        json=_expense(account["id"], description="old"),
    ).json()
    res = client.patch(
        f"/users/{user_id}/transactions/{txn['id']}", json={"description": "updated"}
    )
    assert res.status_code == 200
    assert res.json()["description"] == "updated"


# ---------------------------------------------------------------------------
# DELETE /users/{user_id}/transactions/{transaction_id}
# ---------------------------------------------------------------------------


def test_delete_transaction_returns_204(
    client: TestClient, user_id: str, account: dict
):
    created = client.post(
        f"/users/{user_id}/transactions", json=_income(account["id"])
    ).json()
    assert (
        client.delete(f"/users/{user_id}/transactions/{created['id']}").status_code
        == 204
    )


def test_deleted_transaction_not_in_list(
    client: TestClient, user_id: str, account: dict
):
    created = client.post(
        f"/users/{user_id}/transactions", json=_income(account["id"])
    ).json()
    client.delete(f"/users/{user_id}/transactions/{created['id']}")
    ids = [t["id"] for t in client.get(f"/users/{user_id}/transactions").json()]
    assert created["id"] not in ids


def test_delete_transaction_wrong_user_returns_404(
    client: TestClient, user_id: str, account: dict
):
    created = client.post(
        f"/users/{user_id}/transactions", json=_income(account["id"])
    ).json()
    res = client.delete(f"/users/{uuid.uuid4()}/transactions/{created['id']}")
    assert res.status_code == 404
