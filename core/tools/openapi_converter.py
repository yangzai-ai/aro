"""Convert OpenAPI 3.0 JSON/YAML operations into executable HTTP tools.

Supports local references, path/query/header parameters with primitive schemas,
and JSON request bodies. Unsupported parameter serialization and security schemes
fail explicitly at conversion; callers must not mistake partial mappings for a
complete OpenAPI client. HTTP credentials belong in a separate adapter.
"""

import copy
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import yaml
from jsonschema import Draft4Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from core.tools.base import BaseTool, PermissionLevel, ToolResult

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _resolve(value: Any, spec: dict[str, Any], trail: tuple[str, ...] = ()) -> Any:
    """Resolve local JSON pointers, rejecting external and recursive references."""
    if isinstance(value, list):
        return [_resolve(item, spec, trail) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        reference = value["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise ValueError("Only local OpenAPI $ref values are supported")
        if reference in trail:
            raise ValueError(f"Recursive OpenAPI reference is unsupported: {reference}")
        target: Any = spec
        try:
            for part in reference[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Unresolved OpenAPI reference: {reference}") from exc
        return _resolve(copy.deepcopy(target), spec, (*trail, reference))
    result = {key: _resolve(item, spec, trail) for key, item in value.items()}
    # OpenAPI 3.0 nullable is not a JSON Schema keyword.
    if result.pop("nullable", False) and "type" in result:
        result["type"] = [result["type"], "null"]
    return result


class OpenAPITool(BaseTool):
    """A single HTTP operation backed by an OpenAPI-derived argument schema."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        method: str,
        path: str,
        base_url: str,
        permission: PermissionLevel,
        schema: dict[str, Any],
        parameters: list[dict[str, Any]],
        has_body: bool,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Store a validated operation; optional transport isolates tests from networks."""
        self._name = name
        self._description = description
        self.method = method.upper()
        self.path = path
        self.base_url = base_url.rstrip("/")
        self._permission = permission
        self._schema = schema
        self.parameters = parameters
        self.has_body = has_body
        self.transport = transport
        self.validator = Draft4Validator(schema, format_checker=FormatChecker())

    @property
    def name(self) -> str:
        """Operation identifier or method/path-derived function name."""
        return self._name

    @property
    def description(self) -> str:
        """Summary supplied by the API specification."""
        return self._description

    @property
    def usage_prompt(self) -> str:
        """Explain operation choice, parameter binding and permission limitations."""
        return (
            f"Use for {self.description} ({self.method} {self.path}). "
            "Do not use for unrelated API operations; follow the argument schema. "
            "Pass path/query/header arguments by name and JSON request payload as body. "
            "Required fields must be supplied; never invent identifiers. "
            f"Permission: {self.permission}. Write operations require HITL (v0.4); "
            "forbidden operations cannot execute. HTTP errors are failures, not empty results."
        )

    @property
    def permission(self) -> PermissionLevel:
        """Configured permission (non-read HTTP methods are at least write)."""
        return self._permission

    @property
    def schema(self) -> dict[str, Any]:
        """JSON Schema for LLM-visible and runtime-validated arguments."""
        return copy.deepcopy(self._schema)

    async def run(self, **kwargs: Any) -> ToolResult:
        """Validate and map arguments, execute HTTP, and preserve structured responses."""
        # Agent-originated write authorization belongs to KernelLoop hook.
        # Direct run() calls are trusted application code, like other BaseTools.
        if self.permission == "forbidden":
            return ToolResult(
                success=False,
                error="Permission denied: forbidden operations cannot execute",
            )
        try:
            self.validator.validate(kwargs)
        except ValidationError as exc:
            return ToolResult(success=False, error=f"Invalid API arguments: {exc.message}")

        path = self.path
        query: dict[str, str] = {}
        headers: dict[str, str] = {}
        for parameter in self.parameters:
            name = parameter["name"]
            if name not in kwargs:
                continue
            raw = kwargs[name]
            value = str(raw).lower() if isinstance(raw, bool) else str(raw)
            location = parameter["in"]
            if location == "path":
                path = path.replace("{" + name + "}", quote(value, safe=""))
            elif location == "query":
                query[name] = value
            else:
                headers[name] = value
        request: dict[str, Any] = {"params": query, "headers": headers}
        if self.has_body and "body" in kwargs:
            request["json"] = kwargs["body"]
        try:
            async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
                response = await client.request(self.method, self.base_url + path, **request)
                response.raise_for_status()
            if not response.content:
                output: Any = None
            elif "json" in response.headers.get("content-type", ""):
                output = response.json()
            else:
                output = response.text
            return ToolResult(success=True, output=output)
        except httpx.HTTPStatusError as exc:
            return ToolResult(success=False, error=f"API returned HTTP {exc.response.status_code}")
        except httpx.RequestError:
            return ToolResult(
                success=False, error="API request failed; check connectivity or retry later"
            )
        except ValueError:
            return ToolResult(success=False, error="API returned invalid JSON")


def convert_openapi_to_tools(
    spec_path: str | Path,
    base_url: str,
    permission: PermissionLevel = "read",
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[BaseTool]:
    """Read a 3.0 spec and generate executable tools without making requests.

    Mutation methods are tagged write even when the default permission is read.
    Unsupported security, complex parameter serialization or non-JSON request
    bodies are rejected instead of producing a silently incorrect client.
    """
    url = httpx.URL(base_url)
    if url.scheme not in {"http", "https"} or not url.host or url.query or url.fragment:
        raise ValueError("base_url must be an absolute HTTP(S) URL without query/fragment")
    if permission not in {"read", "write", "forbidden"}:
        raise ValueError("Unknown permission")
    with Path(spec_path).open(encoding="utf-8") as file:
        spec = yaml.safe_load(file)
    if not isinstance(spec, dict) or not str(spec.get("openapi", "")).startswith("3.0."):
        raise ValueError("Expected an OpenAPI 3.0 specification")
    if not isinstance(spec.get("paths"), dict):
        raise ValueError("OpenAPI paths must be an object")
    tools: list[BaseTool] = []
    names: set[str] = set()
    for path, raw_item in spec["paths"].items():
        if not isinstance(path, str) or not path.startswith("/") or "?" in path or "#" in path:
            raise ValueError("OpenAPI paths must start with / and contain no query/fragment")
        item = _resolve(raw_item, spec)
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            operation = _resolve(operation, spec)
            if operation.get("security", spec.get("security")):
                raise ValueError("Authenticated operations require a dedicated API adapter")
            name = operation.get("operationId") or re.sub(
                r"[^a-zA-Z0-9_]+", "_", f"{method}_{path}"
            ).strip("_")
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) or name in names:
                raise ValueError(f"Invalid or duplicate operation name: {name}")
            names.add(name)
            # Operation-level parameters override the same path-level (name, in).
            combined = {
                (parameter["name"], parameter["in"]): parameter
                for parameter in [*item.get("parameters", []), *operation.get("parameters", [])]
            }
            parameters = list(combined.values())
            properties: dict[str, Any] = {}
            required: list[str] = []
            for parameter in parameters:
                param_name = parameter["name"]
                location = parameter["in"]
                param_schema = parameter.get("schema", {})
                if location not in {"path", "query", "header"}:
                    raise ValueError(f"Unsupported parameter location: {location}")
                if param_schema.get("type") not in {"string", "integer", "number", "boolean"}:
                    raise ValueError(f"Only primitive HTTP parameters are supported: {param_name}")
                default_style = {"path": "simple", "query": "form", "header": "simple"}[location]
                if parameter.get("style", default_style) != default_style:
                    raise ValueError(f"Unsupported parameter serialization: {param_name}")
                if param_name in properties or param_name == "body":
                    raise ValueError(f"Ambiguous parameter name: {param_name}")
                properties[param_name] = param_schema
                if parameter.get("required") or location == "path":
                    required.append(param_name)
            placeholders = set(re.findall(r"\{([^{}]+)\}", path))
            if placeholders != {p["name"] for p in parameters if p["in"] == "path"}:
                raise ValueError(f"Path parameters do not match template: {path}")
            body = operation.get("requestBody")
            if body is not None:
                content = body.get("content", {})
                if "application/json" not in content:
                    raise ValueError("Only application/json request bodies are supported")
                properties["body"] = content["application/json"].get("schema", {})
                if body.get("required"):
                    required.append("body")
            schema = {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            }
            if not required:
                del schema["required"]
            Draft4Validator.check_schema(schema)
            effective_permission = permission
            if permission == "read" and method not in {"get", "head", "options"}:
                effective_permission = "write"
            tools.append(
                OpenAPITool(
                    name=name,
                    description=operation.get("summary")
                    or operation.get("description")
                    or f"{method.upper()} {path}",
                    method=method,
                    path=path,
                    base_url=base_url,
                    permission=effective_permission,
                    schema=schema,
                    parameters=parameters,
                    has_body=body is not None,
                    transport=transport,
                )
            )
    return tools
