import requests
import os

def create_prodigi_order(order):
    """
    Formats an OpenEire order and sends it to the Prodigi API.
    """
    is_sandbox = os.environ.get("PRODIGI_SANDBOX", "True") == "True"
    base_url = "https://api.sandbox.prodigi.com/v4.0/" if is_sandbox else "https://api.prodigi.com/v4.0/"
    url = f"{base_url}orders"
    api_key = os.environ.get("PRODIGI_API_KEY")
    site_url = os.environ.get("SITE_URL", "http://127.0.0.1:8000")

    if not api_key:
        print("PRODIGI ERROR: No API Key found in environment.")
        return

    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }

    items_payload = []
    for item in order.items.all():
        product = item.product
        
        # Uses your original variant-level SKU lookup!
        if hasattr(product, 'prodigi_sku') and product.prodigi_sku:
            try:
                raw_url = product.photo.high_res_file.url
                image_url = raw_url if raw_url.startswith('http') else f"{site_url}{raw_url}"
                
                if "127.0.0.1" in image_url or "localhost" in image_url:
                    print(f"⚠️  WARNING: Prodigi cannot download from localhost ({image_url}).")

                items_payload.append({
                    "sku": product.prodigi_sku,
                    "copies": item.quantity,
                    "sizing": "fillPrintArea",
                    "assets": [{"printArea": "default", "url": image_url}]
                })

            except Exception as e:
                print(f"Error getting image URL for {product}: {e}")
                continue

    if not items_payload:
        print("No physical items found for Prodigi fulfillment.")
        return None

    address_payload = {
        "line1": order.street_address1,
        "postalOrZipCode": order.postcode,
        "countryCode": str(order.country),
        "townOrCity": order.town,
        "stateOrCounty": order.county
    }

    if order.street_address2 and order.street_address2.strip():
        address_payload["line2"] = order.street_address2

    prodigi_shipping_method = order.shipping_method.capitalize()

    payload = {
        "shippingMethod": prodigi_shipping_method,
        "recipient": {
            "name": f"{order.first_name}",
            "address": address_payload,
            "email": order.email
        },
        "items": items_payload,
        "idempotencyKey": order.order_number
    }

    response = requests.post(url, json=payload, headers=headers)
    
    if 200 <= response.status_code < 300:
        data = response.json()
        print(f"✅ SUCCESS: Prodigi Order Created. ID: {data['order']['id']}")
        return data
    else:
        print(f"PRODIGI FAILED: {response.status_code} - {response.text}")
        raise Exception(f"Prodigi API Error: {response.text}")