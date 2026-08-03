"""Retired: gateway-behavior tests moved to test_gateway_retirement.py.

The custom Node health gateway (``scripts/opencode_health_gateway.js``)
is retired under the container-sidecar contract.  All gateway-behavior
assertions and live gateway test classes have been removed.

The retirement assertions (script absence, reference absence, Dockerfile
runtime-path absence) now live in ``tests/test_gateway_retirement.py``.
"""
