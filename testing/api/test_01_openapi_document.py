"""TS-API-01  The Swagger / OpenAPI document itself (static contract checks).

Technique: specification-based review of the contract (ISO/IEC/IEEE 29119-4
"specification-based" family) before any request is sent.
"""
import requests
from openapi_spec_validator import validate


def test_api_01_001_openapi_document_is_valid(spec):
    """The published document validates against the OpenAPI 3.x meta-schema."""
    assert spec["openapi"].startswith("3."), spec["openapi"]
    validate(spec)  # raises OpenAPIValidationError with the exact path on failure


def test_api_01_002_every_operation_has_a_unique_operation_id(spec):
    ids = [op.get("operationId") for path in spec["paths"].values()
           for m, op in path.items() if m in {"get", "post", "put", "patch", "delete"}]
    assert all(ids), "an operation without operationId cannot be addressed by Swagger clients"
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate operationIds: {sorted(dupes)[:10]}"


def test_api_01_003_every_operation_documents_a_success_response(spec):
    missing = [f"{m.upper()} {p}" for p, path in spec["paths"].items()
               for m, op in path.items() if m in {"get", "post", "put", "patch", "delete"}
               and not any(code.startswith("2") for code in op.get("responses", {}))]
    assert not missing, missing[:10]


def test_api_01_004_swagger_ui_and_redoc_are_served(base):
    docs = requests.get(f"{base}/docs", timeout=10)
    assert docs.status_code == 200 and "swagger-ui" in docs.text.lower()
    redoc = requests.get(f"{base}/redoc", timeout=10)
    assert redoc.status_code == 200 and "redoc" in redoc.text.lower()


def test_api_01_005_document_inventory(spec, record_property):
    """Not an assertion so much as a measurement: the size of the surface under test."""
    ops = [(m, p) for p, path in spec["paths"].items() for m in path if m in {"get", "post", "put", "patch", "delete"}]
    by_method = {m: sum(1 for x, _ in ops if x == m) for m in {"get", "post", "put", "patch", "delete"}}
    record_property("paths", len(spec["paths"]))
    record_property("operations", len(ops))
    for m, n in by_method.items():
        record_property(f"ops_{m}", n)
    print(f"\nOpenAPI inventory: {len(spec['paths'])} paths, {len(ops)} operations, {by_method}")
    assert len(ops) > 100
