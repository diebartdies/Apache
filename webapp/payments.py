import json
import os
import urllib.error
import urllib.parse
import urllib.request


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read().decode("utf-8")
    except Exception:
        return str(exc)


def _http_post_form(url: str, form_data: list[tuple[str, str]], headers: dict[str, str] | None = None) -> dict:
    encoded = urllib.parse.urlencode(form_data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    req = urllib.request.Request(url, method="GET", headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _stripe_secret_key() -> str:
    return os.environ.get("STRIPE_SECRET_KEY", "").strip()


def _mercadopago_access_token() -> str:
    return os.environ.get("MERCADOPAGO_ACCESS_TOKEN", "").strip()


def _stripe_currency() -> str:
    return os.environ.get("STRIPE_CURRENCY", "usd").strip().lower() or "usd"


def _mercadopago_currency() -> str:
    return os.environ.get("MERCADOPAGO_CURRENCY", "usd").strip().upper() or "USD"


def configured_providers() -> list[dict[str, str]]:
    providers: list[dict[str, str]] = []
    if _stripe_secret_key():
        providers.append({"code": "stripe", "name": "Stripe", "description": "Cards and global checkout"})
    if _mercadopago_access_token():
        providers.append({"code": "mercadopago", "name": "Mercado Pago", "description": "Latin America and local payment methods"})
    return providers


def _price_to_minor_units(price: float) -> int:
    return max(50, int(round(price * 100)))


def create_checkout(provider: str, product: dict, base_url: str, buyer_ref: str = "") -> tuple[str, str]:
    if provider == "stripe":
        return _create_stripe_checkout(product, base_url, buyer_ref)
    if provider == "mercadopago":
        return _create_mercadopago_checkout(product, base_url, buyer_ref)
    return "", "Unsupported payment provider."


def _create_stripe_checkout(product: dict, base_url: str, buyer_ref: str = "") -> tuple[str, str]:
    secret_key = _stripe_secret_key()
    if not secret_key:
        return "", "Stripe is not configured. Set STRIPE_SECRET_KEY."

    success_url = f"{base_url}/checkout/success?provider=stripe&id={urllib.parse.quote(product['id'], safe='')}&session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{base_url}/checkout/cancel?provider=stripe&id={urllib.parse.quote(product['id'], safe='')}"
    form_data = [
        ("mode", "payment"),
        ("success_url", success_url),
        ("cancel_url", cancel_url),
        ("line_items[0][price_data][currency]", _stripe_currency()),
        ("line_items[0][price_data][product_data][name]", str(product["name"])),
        ("line_items[0][price_data][unit_amount]", str(_price_to_minor_units(float(product["price"])))),
        ("line_items[0][quantity]", "1"),
        ("metadata[product_id]", str(product["id"])),
    ]
    if buyer_ref:
        form_data.append(("client_reference_id", buyer_ref[:200]))
        form_data.append(("metadata[buyer_ref]", buyer_ref[:200]))

    try:
        response = _http_post_form(
            "https://api.stripe.com/v1/checkout/sessions",
            form_data,
            headers={"Authorization": f"Bearer {secret_key}"},
        )
    except urllib.error.HTTPError as exc:
        return "", f"Stripe checkout creation failed: {_http_error_message(exc)}"
    except Exception as exc:
        return "", f"Stripe checkout creation failed: {exc}"

    checkout_url = (response.get("url") or "").strip()
    if not checkout_url:
        return "", "Stripe did not return a checkout URL."
    return checkout_url, ""


def _create_mercadopago_checkout(product: dict, base_url: str, buyer_ref: str = "") -> tuple[str, str]:
    access_token = _mercadopago_access_token()
    if not access_token:
        return "", "Mercado Pago is not configured. Set MERCADOPAGO_ACCESS_TOKEN."

    product_id = urllib.parse.quote(product["id"], safe="")
    payload: dict = {
        "items": [
            {
                "id": str(product["id"]),
                "title": str(product["name"]),
                "quantity": 1,
                "currency_id": _mercadopago_currency(),
                "unit_price": float(product["price"]),
            }
        ],
        "external_reference": str(product["id"]),
        "back_urls": {
            "success": f"{base_url}/checkout/success?provider=mercadopago&id={product_id}",
            "failure": f"{base_url}/checkout/cancel?provider=mercadopago&id={product_id}",
            "pending": f"{base_url}/checkout/success?provider=mercadopago&id={product_id}",
        },
        "auto_return": "approved",
    }
    if buyer_ref and "@" in buyer_ref:
        payload["payer"] = {"email": buyer_ref}

    try:
        response = _http_post_json(
            "https://api.mercadopago.com/checkout/preferences",
            payload,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except urllib.error.HTTPError as exc:
        return "", f"Mercado Pago checkout creation failed: {_http_error_message(exc)}"
    except Exception as exc:
        return "", f"Mercado Pago checkout creation failed: {exc}"

    is_test_token = access_token.upper().startswith("TEST")
    checkout_url = (response.get("sandbox_init_point") if is_test_token else response.get("init_point") or response.get("sandbox_init_point") or "").strip()
    if not checkout_url:
        return "", "Mercado Pago did not return a checkout URL."
    return checkout_url, ""


def verify_checkout(provider: str, query: dict[str, str], expected_product_id: str = "") -> tuple[bool, str, dict]:
    if provider == "stripe":
        return _verify_stripe_checkout(query, expected_product_id)
    if provider == "mercadopago":
        return _verify_mercadopago_checkout(query, expected_product_id)
    return False, "Unsupported payment provider.", {}


def _verify_stripe_checkout(query: dict[str, str], expected_product_id: str = "") -> tuple[bool, str, dict]:
    secret_key = _stripe_secret_key()
    if not secret_key:
        return False, "Stripe is not configured.", {}

    session_id = (query.get("session_id") or "").strip()
    if not session_id:
        return False, "Missing Stripe session ID.", {}

    try:
        response = _http_get_json(
            f"https://api.stripe.com/v1/checkout/sessions/{urllib.parse.quote(session_id, safe='')}",
            headers={"Authorization": f"Bearer {secret_key}"},
        )
    except urllib.error.HTTPError as exc:
        return False, f"Stripe verification failed: {_http_error_message(exc)}", {}
    except Exception as exc:
        return False, f"Stripe verification failed: {exc}", {}

    product_id = ((response.get("metadata") or {}).get("product_id") or expected_product_id or "").strip()
    if expected_product_id and product_id and product_id != expected_product_id:
        return False, "Stripe payment did not match the expected product.", {}

    payment_status = (response.get("payment_status") or "").strip().lower()
    if payment_status != "paid":
        return False, f"Stripe payment is not complete yet (status: {payment_status or 'unknown'}).", {}

    return True, "", {
        "provider_name": "Stripe",
        "provider_code": "stripe",
        "product_id": product_id,
        "payment_id": response.get("payment_intent") or response.get("id") or session_id,
        "status": payment_status,
        "buyer": response.get("customer_details", {}).get("email") or response.get("client_reference_id") or "",
        "amount_total": response.get("amount_total"),
        "currency": (response.get("currency") or "").upper(),
        "amount_is_minor": True,
    }


def _verify_mercadopago_checkout(query: dict[str, str], expected_product_id: str = "") -> tuple[bool, str, dict]:
    access_token = _mercadopago_access_token()
    if not access_token:
        return False, "Mercado Pago is not configured.", {}

    payment_id = (query.get("payment_id") or query.get("collection_id") or "").strip()
    if not payment_id:
        return False, "Missing Mercado Pago payment ID.", {}

    try:
        response = _http_get_json(
            f"https://api.mercadopago.com/v1/payments/{urllib.parse.quote(payment_id, safe='')}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except urllib.error.HTTPError as exc:
        return False, f"Mercado Pago verification failed: {_http_error_message(exc)}", {}
    except Exception as exc:
        return False, f"Mercado Pago verification failed: {exc}", {}

    product_id = (response.get("external_reference") or expected_product_id or "").strip()
    if expected_product_id and product_id and product_id != expected_product_id:
        return False, "Mercado Pago payment did not match the expected product.", {}

    status = (response.get("status") or "").strip().lower()
    if status != "approved":
        return False, f"Mercado Pago payment is not approved yet (status: {status or 'unknown'}).", {}

    payer = response.get("payer") or {}
    transaction_amount = response.get("transaction_amount")
    return True, "", {
        "provider_name": "Mercado Pago",
        "provider_code": "mercadopago",
        "product_id": product_id,
        "payment_id": response.get("id") or payment_id,
        "status": status,
        "buyer": payer.get("email") or "",
        "amount_total": transaction_amount,
        "currency": (response.get("currency_id") or "").upper(),
        "amount_is_minor": False,
    }
