from pathlib import Path


def bar() -> None:
    with Path("/dev/null").open(mode="wb") as f:
        f.write(b"foo")
