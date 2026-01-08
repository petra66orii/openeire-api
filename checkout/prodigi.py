import requests
import os
import json
from django.conf import settings

def create_prodigi_order(order):
    """
    Formats an OpenEire order and sends it to the Prodigi API.
    """
    # 1. Setup Environment
    is_sandbox = os.environ.get("PRODIGI_SANDBOX", "True") == "True"
    base_url = "https://api.sandbox.prodigi.com/v4.0/" if is_sandbox else "https://api.prodigi.com/v4.0/"
    url = f"{base_url}orders"
    api_key = os.environ.get("PRODIGI_API_KEY")
    
    # Define your domain for image URLs (Use Ngrok URL if testing locally!)
    # In production, this should be your real domain (e.g. https://openeire.com)
    site_url = os.environ.get("SITE_URL", "http://127.0.0.1:8000")

    if not api_key:
        print("PRODIGI ERROR: No API Key found in environment.")
        return

    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }

    # 2. Build Items Payload
    items_payload = []
    for item in order.items.all():
        product = item.product
        
        # Check for Physical Variant (has prodigi_sku)
        if hasattr(product, 'prodigi_sku') and product.prodigi_sku:
            try:
                # Get the URL
                raw_url = product.photo.high_res_file.url
                
                if not raw_url.startswith('http'):
                    image_url = f"{site_url}{raw_url}"
                else:
                    image_url = raw_url
                
                # Warning for Localhost
                if "127.0.0.1" in image_url or "localhost" in image_url:
                    print(f"⚠️  WARNING: Prodigi cannot download images from localhost ({image_url}). Order will be created but assets will fail.")

            except Exception as e:
                print(f"Error getting image URL for {product}: {e}")
                continue

            items_payload.append({
                "sku": product.prodigi_sku,
                "copies": item.quantity,
                "sizing": "fillPrintArea",
                "assets": [{"printArea": "default", "url": image_url}]
            })

    if not items_payload:
        print("No physical items found for Prodigi fulfillment.")
        return None

    # 3. Build Address Dictionary Dynamically
    address_payload = {
        "line1": order.street_address1,
        "postalOrZipCode": order.postcode,
        "countryCode": str(order.country),
        "townOrCity": order.town,
        "stateOrCounty": order.county
    }

    # Only add line2 if it is NOT empty
    if order.street_address2 and order.street_address2.strip():
        address_payload["line2"] = order.street_address2

    # 4. Construct Final Payload
    payload = {
        "shippingMethod": "Budget",
        "recipient": {
            "name": f"{order.first_name}",
            "address": address_payload,
            "email": order.email
        },
        "items": items_payload,
        "idempotencyKey": order.order_number
    }

    # 5. Send Request
    response = requests.post(url, json=payload, headers=headers)
    
    if 200 <= response.status_code < 300:
        data = response.json()
        print(f"✅ SUCCESS: Prodigi Order Created. ID: {data['order']['id']}")
        return data
    else:
        print(f"PRODIGI FAILED: {response.status_code} - {response.text}")
        raise Exception(f"Prodigi API Error: {response.text}")