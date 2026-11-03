"""Resolve trusted runtime identity, never model-supplied tool arguments."""

from core.kernel.state import AgentState

Namespace = tuple[str, ...]


def memory_namespace(state: AgentState) -> Namespace:
    """Stable users share across threads; anonymous callers stay thread-scoped."""
    context = state.get("context", {})
    top_tenant, tenant = state.get("tenant_id"), context.get("tenant_id")
    if top_tenant and tenant and top_tenant != tenant:
        raise ValueError("Conflicting tenant identity")
    tenant = tenant or top_tenant or "local"
    user = context.get("user_id")
    scope, identity = ("user", user) if user is not None else ("thread", context.get("thread_id"))
    if (
        not isinstance(tenant, str)
        or not tenant.strip()
        or not isinstance(identity, str)
        or not identity.strip()
    ):
        raise ValueError("Memory requires a nonempty trusted user_id or thread_id")
    return ("aro", "l2", tenant, scope, identity)
