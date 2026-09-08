from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from harness.core.errors import ConflictError, NotFoundError
from harness.studio.builder_materials import BuilderMaterialsRequest, read_builder_materials


@pytest.mark.asyncio
async def test_reference_text_is_read_as_data_without_running_a_model() -> None:
    files, models = AsyncMock(), AsyncMock()
    files.resolve_for_run.return_value = [SimpleNamespace(
        input_artifact_id="a", name="format.txt", media_type="text/plain", size_bytes=12,
    )]
    files.download.return_value = (None, "输出三列表格".encode())
    result = await read_builder_materials(
        "tenant", "owner", BuilderMaterialsRequest(inputArtifactIds=["a"]), files, models,
    )
    assert "输出三列表格" in result["context"]
    models.complete_text.assert_not_called()
    files.resolve_for_run.assert_awaited_once_with(
        tenant_id="tenant", user_id="owner", input_artifact_ids=("a",),
    )


@pytest.mark.asyncio
async def test_image_uses_available_vision_and_never_text_only_route() -> None:
    files, models = AsyncMock(), AsyncMock()
    files.resolve_for_run.return_value = [SimpleNamespace(
        input_artifact_id="a", name="sample.png", media_type="image/png", size_bytes=100,
    )]
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, "PNG")
    files.download.return_value = (None, buffer.getvalue())
    routes = [SimpleNamespace(
        route_id=name, enabled=True, credential_configured=True, model_type="chat",
        api_format="openai_compatible", capabilities=caps,
    ) for name, caps in [("text", ("tool_use",)), ("vision", ("vision",))]]
    models.list.return_value = SimpleNamespace(models=routes)
    models.complete_text.return_value = "参考图是红色方块"
    result = await read_builder_materials(
        "tenant", "owner", BuilderMaterialsRequest(inputArtifactIds=["a"], modelRoute="text"),
        files, models,
    )
    assert result["context"] == "参考图是红色方块"
    call = models.complete_text.call_args
    assert call.args == ("tenant", "vision")
    assert call.kwargs["images"][0][0] == "image/jpeg"
    assert Image.open(BytesIO(call.kwargs["images"][0][1])).size == (8, 8)
    models.list.return_value = SimpleNamespace(models=routes[:1])
    with pytest.raises(ConflictError, match="视觉模型"):
        await read_builder_materials(
            "tenant", "owner", BuilderMaterialsRequest(inputArtifactIds=["a"]), files, models,
        )


@pytest.mark.asyncio
async def test_foreign_reference_is_rejected_before_download() -> None:
    files, models = AsyncMock(), AsyncMock()
    files.resolve_for_run.side_effect = NotFoundError("not owned")
    with pytest.raises(NotFoundError):
        await read_builder_materials(
            "tenant", "other", BuilderMaterialsRequest(inputArtifactIds=["a"]), files, models,
        )
    files.download.assert_not_called()
    models.complete_text.assert_not_called()
