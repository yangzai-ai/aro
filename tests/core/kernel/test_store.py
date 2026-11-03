"""Neutral KV items, process-local sharing and namespace paging."""

from uuid import uuid4

from core.kernel.store import InMemoryKVStore, KVItem, shared_store


def test_named_default_store_sharing_and_isolation():
    name = str(uuid4())
    first = shared_store(name)
    assert isinstance(first, InMemoryKVStore)
    assert shared_store(name) is first
    assert shared_store(str(uuid4())) is not first


async def test_default_store_neutral_items_prefix_paging_and_overwrite():
    store = InMemoryKVStore()
    namespace = ("tenant", "user")
    await store.aput(namespace, "key", {"text": "old"})
    await store.aput(namespace, "key", {"text": "updated"})
    await store.aput(namespace + ("child",), "child", {"text": "child"})
    await store.aput(("another",), "other", {"text": "secret"})
    first = await store.asearch(namespace, limit=1, offset=0)
    second = await store.asearch(namespace, limit=1, offset=1)
    assert len(first) == len(second) == 1
    assert all(type(item) is KVItem for item in first + second)
    assert {(item.namespace, item.value["text"]) for item in first + second} == {
        (namespace, "updated"),
        (namespace + ("child",), "child"),
    }
    assert await store.asearch(namespace, limit=1, offset=2) == []
    assert await InMemoryKVStore().asearch(namespace) == []
