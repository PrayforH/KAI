"""Content-addressed Skill bodies behind the existing repository contracts.

Domain/API payloads remain self-contained. Only the persisted representation
uses references, so legacy rows, exports and runtime hashes remain compatible.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import re
from collections import OrderedDict
from typing import Any, cast

from harness.core.errors import NotFoundError
from harness.core.ports import ArtifactStore

_REF = "_skillBlob"
_MAX_BODY = 384 * 1024 * 1024
_CACHE_BYTES = 64 * 1024 * 1024


class SkillBlobStore:
    def __init__(self, store: ArtifactStore) -> None:
        self._store = store
        self._cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._cache_bytes = 0

    def _remember(self, key: tuple[str, str], body: bytes) -> None:
        previous = self._cache.pop(key, None)
        if previous is not None:
            self._cache_bytes -= len(previous)
        if len(body) > _CACHE_BYTES:
            return
        self._cache[key] = body
        self._cache_bytes += len(body)
        while self._cache_bytes > _CACHE_BYTES:
            _, removed = self._cache.popitem(last=False)
            self._cache_bytes -= len(removed)

    @staticmethod
    def _decode(body: bytes, digest: str) -> dict[str, Any]:
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
            raw = stream.read(_MAX_BODY + 1)
        if len(raw) > _MAX_BODY or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Skill blob integrity check failed")
        value = json.loads(raw)
        if not isinstance(value, dict) or set(cast(dict[str, Any], value)) - {
            "instructions",
            "files",
        }:
            raise ValueError("Invalid Skill blob body")
        return cast(dict[str, Any], value)

    async def _read(self, tenant_id: str, digest: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid Skill blob reference")
        key = (tenant_id, digest)
        body = self._cache.get(key)
        if body is None:
            body = await self._store.get(tenant_id, f"skill-blobs/v1/{digest}.json.gz")
        result = self._decode(body, digest)
        self._remember(key, body)
        return result

    async def _pack(self, tenant_id: str, skill: dict[str, Any]) -> dict[str, Any]:
        if _REF in skill:
            # Idempotent migration also verifies that the durable object exists.
            await self._unpack(tenant_id, skill)
            return skill
        body = {key: skill[key] for key in ("instructions", "files") if key in skill}
        if not body:
            return skill
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        if len(raw) > _MAX_BODY:
            raise ValueError("Skill blob exceeds storage limit")
        digest = hashlib.sha256(raw).hexdigest()
        try:
            await self._read(tenant_id, digest)
        except NotFoundError:
            encoded = gzip.compress(raw, compresslevel=1, mtime=0)
            await self._store.put(tenant_id, f"skill-blobs/v1/{digest}.json.gz", encoded)
            self._remember((tenant_id, digest), encoded)
        return {**{k: v for k, v in skill.items() if k not in body}, _REF: digest}

    async def _unpack(self, tenant_id: str, skill: dict[str, Any]) -> dict[str, Any]:
        if _REF not in skill:
            return skill
        if any(key in skill for key in ("instructions", "files")):
            raise ValueError("Skill contains both inline data and a blob reference")
        body = await self._read(tenant_id, skill[_REF])
        return {**{k: v for k, v in skill.items() if k != _REF}, **body}

    async def transform(
        self,
        tenant_id: str,
        payload: dict[str, Any],
        *,
        inline: bool = False,
    ) -> dict[str, Any]:
        result = copy.deepcopy(payload)
        operation = self._unpack if inline else self._pack
        # Drafts and published/validated versions use different domain schemas.
        for container, field in (
            (result.get("spec"), "skills"),
            (result.get("snapshot"), "skill_snapshots"),
        ):
            if isinstance(container, dict):
                mapping = cast(dict[str, Any], container)
                values = mapping.get(field)
                if isinstance(values, list):
                    mapping[field] = [
                        await operation(tenant_id, item)
                        for item in cast(list[dict[str, Any]], values)
                    ]
        return result
