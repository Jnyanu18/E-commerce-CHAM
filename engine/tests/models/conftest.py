def pytest_configure(config):
    config.addinivalue_line("markers", "slow: heavy sampling tests; run with RUN_SLOW=1")
