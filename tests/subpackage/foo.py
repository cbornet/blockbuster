from pathlib import Path


def bar(path: Path) -> None:
    with path.open(mode="wb") as f:
        f.write(b"foo")
