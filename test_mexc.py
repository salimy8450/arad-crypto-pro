import requests

BASE_URL = "https://contract.mexc.com"

tests = [
    ("PING", f"{BASE_URL}/api/v1/contract/ping"),
    ("CONTRACTS", f"{BASE_URL}/api/v1/contract/detail"),
]

for name, url in tests:
    print(f"\nTesting {name}...")

    try:
        response = requests.get(url, timeout=20)
        print("HTTP:", response.status_code)
        print("Response:", response.text[:500])
        response.raise_for_status()

        data = response.json()
        print("API success:", data.get("success"))

    except Exception as e:
        print("ERROR:", str(e))
