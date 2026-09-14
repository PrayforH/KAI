from io import BytesIO
from zipfile import ZipFile

from harness.studio.deepagents_export import DeepagentsProjectArchive, project_source


def test_source_preview_preserves_text_and_bounds_binary_and_large_files() -> None:
    data = BytesIO()
    with ZipFile(data, "w") as zipped:
        zipped.writestr("agent.py", "print('中文')\n")
        zipped.writestr("skills/data/image.png", b"\x89PNG\0")
        zipped.writestr("empty.py", "")
        zipped.writestr("large.txt", "x" * (256 * 1024 + 1))
        for index in range(10):
            zipped.writestr(f"data{index}.txt", "a" * (256 * 1024))
    view = project_source(DeepagentsProjectArchive(data.getvalue(), "agent.zip"), 8)
    files = {entry.path: entry for entry in view.files}
    assert files["agent.py"].content == "print('中文')\n"
    assert files["empty.py"].content == ""
    assert files["skills/data/image.png"].content is None
    assert files["skills/data/image.png"].unavailable
    assert files["large.txt"].content is None
    assert files["data9.txt"].content is None
    assert sum(len((entry.content or "").encode()) for entry in view.files) <= 2 * 1024 * 1024
    assert view.revision == 8
