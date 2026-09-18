from io import BytesIO
from zipfile import ZipFile

from harness.studio.deepagents_export import (
    PROJECT_SOURCE_FILE_LIMIT,
    PROJECT_SOURCE_TOTAL_LIMIT,
    DeepagentsProjectArchive,
    project_source,
)


def test_source_preview_preserves_text_and_bounds_binary_and_large_files() -> None:
    data = BytesIO()
    with ZipFile(data, "w") as zipped:
        zipped.writestr("agent.py", "print('中文')\n")
        zipped.writestr("skills/data/image.png", b"\x89PNG\0")
        zipped.writestr("empty.py", "")
        zipped.writestr("large.txt", "x" * (PROJECT_SOURCE_FILE_LIMIT + 1))
        for index in range(10):
            zipped.writestr(f"data{index}.txt", "a" * PROJECT_SOURCE_FILE_LIMIT)
    view = project_source(DeepagentsProjectArchive(data.getvalue(), "agent.zip"), 8)
    files = {entry.path: entry for entry in view.files}
    assert files["agent.py"].content == "print('中文')\n"
    assert files["empty.py"].content == ""
    assert files["skills/data/image.png"].content is None
    assert files["skills/data/image.png"].unavailable
    assert len(files["data0.txt"].content) == PROJECT_SOURCE_FILE_LIMIT
    assert files["large.txt"].content is None
    assert files["data9.txt"].content is None
    total = sum(len((entry.content or "").encode()) for entry in view.files)
    assert total <= PROJECT_SOURCE_TOTAL_LIMIT
    assert view.revision == 8
