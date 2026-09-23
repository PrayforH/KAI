import copy

import pytest

from harness.adapters.memory import InMemoryArtifactStore
from harness.core.errors import NotFoundError
from harness.storage.skill_blobs import SkillBlobStore


class CountingStore(InMemoryArtifactStore):
    def __init__(self):
        super().__init__()
        self.puts = 0
        self.gets = 0

    async def put(self, *args):
        self.puts += 1
        return await super().put(*args)

    async def get(self, *args):
        self.gets += 1
        return await super().get(*args)


@pytest.mark.asyncio
@pytest.mark.parametrize("container,field", [("spec", "skills"), ("snapshot", "skill_snapshots")])
async def test_roundtrip_dedup_legacy_isolation_and_bounded_cache(container, field):
    store = CountingStore()
    blobs = SkillBlobStore(store)
    original = {
        container: {
            field: [
                {
                    "name": "example",
                    "description": "Example",
                    "instructions": "Instructions",
                    "files": [{"path": "a.bin", "contentBase64": "AA=="}],
                }
            ]
        }
    }
    before = copy.deepcopy(original)
    packed = await blobs.transform("a", original)
    assert original == before
    assert "files" not in packed[container][field][0]
    assert packed[container][field][0]["name"] == "example"
    assert await blobs.transform("a", packed, inline=True) == original
    assert await blobs.transform("a", original, inline=True) == original
    assert await blobs.transform("a", original) == packed
    assert store.puts == 1
    assert store.gets == 1  # verified process cache avoids repeated object reads
    with pytest.raises(NotFoundError):
        await blobs.transform("b", packed, inline=True)
    restarted = SkillBlobStore(store)
    assert await restarted.transform("a", packed, inline=True) == original
    assert await restarted.transform("a", packed) == packed
    assert store.puts == 1
    await blobs.transform("b", original)
    assert store.puts == 2


@pytest.mark.asyncio
async def test_reject_corrupt_missing_or_unsafe_blob_without_inline_fallback():
    store = CountingStore()
    blobs = SkillBlobStore(store)
    payload = {"spec": {"skills": [{"name": "a", "files": []}]}}
    packed = await blobs.transform("a", payload)
    digest = packed["spec"]["skills"][0]["_skillBlob"]
    await store.put("a", f"skill-blobs/v1/{digest}.json.gz", b"corrupt")
    with pytest.raises((ValueError, OSError)):
        await SkillBlobStore(store).transform("a", packed, inline=True)
    packed["spec"]["skills"][0]["_skillBlob"] = "../other"
    with pytest.raises(ValueError, match="reference"):
        await blobs.transform("a", packed, inline=True)
    packed["spec"]["skills"][0]["files"] = []
    with pytest.raises(ValueError, match="both inline"):
        await blobs.transform("a", packed, inline=True)


@pytest.mark.asyncio
async def test_object_write_failure_never_returns_reference():
    class FailedStore(CountingStore):
        async def put(self, *args):
            raise OSError("unavailable")

    with pytest.raises(OSError):
        await SkillBlobStore(FailedStore()).transform(
            "a",
            {"spec": {"skills": [{"files": []}]}},
        )
