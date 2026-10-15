def pytest_configure(config):
    config.addinivalue_line("markers", "slow: heavy sampling tests (need PyMC)")
