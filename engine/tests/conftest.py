import os

# Deterministic test-only secret; production reads CUSTOMER_HASH_KEY from the environment.
os.environ.setdefault("CUSTOMER_HASH_KEY", "test-only-customer-hash-key-0000")
