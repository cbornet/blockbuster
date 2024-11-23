import asyncio
import contextlib
import importlib
import io
import os
import re
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import requests

from blockbuster import BlockBuster, BlockingError, blockbuster_ctx


@pytest.fixture(autouse=True)
def blockbuster() -> Iterator[BlockBuster]:
    with blockbuster_ctx() as bb:
        yield bb


async def test_time_sleep() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("sleep (<module 'time' (built-in)>")
    ):
        time.sleep(1)  # noqa: ASYNC251


async def test_os_read() -> None:
    fd = os.open("/dev/null", os.O_RDONLY)
    with pytest.raises(
        BlockingError, match=re.escape("read (<module 'posix' (built-in)>")
    ):
        os.read(fd, 1)


async def test_os_read_non_blocking() -> None:
    fd = os.open("/dev/null", os.O_NONBLOCK | os.O_RDONLY)
    os.read(fd, 1)


async def test_os_write() -> None:
    fd = os.open("/dev/null", os.O_RDWR)
    with pytest.raises(
        BlockingError, match=re.escape("write (<module 'posix' (built-in)>")
    ):
        os.write(fd, b"foo")


async def test_os_write_non_blocking() -> None:
    fd = os.open("/dev/null", os.O_NONBLOCK | os.O_RDWR)
    os.write(fd, b"foo")


PORT = 65432


def tcp_server() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", PORT))
        s.listen()
        conn, _addr = s.accept()
        with conn:
            conn.sendall(b"Hello, world")
            with contextlib.suppress(ConnectionResetError):
                conn.recv(1024)


async def test_socket_connect() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s,
        pytest.raises(BlockingError, match="method 'connect' of '_socket.socket'"),
    ):
        s.connect(("127.0.0.1", PORT))


async def test_socket_send() -> None:
    t = threading.Thread(target=tcp_server)
    t.start()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        while True:
            try:
                await asyncio.to_thread(s.connect, ("127.0.0.1", PORT))
                break
            except ConnectionRefusedError:
                await asyncio.sleep(0.1)
        with pytest.raises(BlockingError, match="method 'send' of '_socket.socket'"):
            s.send(b"Hello, world")
    await asyncio.to_thread(t.join)


async def test_socket_send_non_blocking() -> None:
    t = threading.Thread(target=tcp_server)
    t.start()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        while True:
            try:
                await asyncio.to_thread(s.connect, ("127.0.0.1", PORT))
                break
            except ConnectionRefusedError:
                await asyncio.sleep(0.1)
        s.setblocking(False)  # noqa: FBT003
        s.send(b"Hello, world")
    await asyncio.to_thread(t.join)


async def test_ssl_socket(blockbuster: BlockBuster) -> None:
    blockbuster.functions["socket.socket.connect"].deactivate()
    with pytest.raises(BlockingError, match="ssl.SSLSocket.send"):
        requests.get("https://google.com", timeout=10)  # noqa: ASYNC210


async def test_file_text() -> None:
    with Path("/dev/null").open(mode="r+", encoding="utf-8") as f:  # noqa: ASYNC230
        assert isinstance(f, io.TextIOWrapper)
        with pytest.raises(
            BlockingError, match="method 'write' of '_io.TextIOWrapper'"
        ):
            f.write("foo")
        with pytest.raises(BlockingError, match="method 'read' of '_io.TextIOWrapper'"):
            f.read(1)


async def test_file_random() -> None:
    with Path("/dev/null").open(mode="r+b") as f:  # noqa: ASYNC230
        assert isinstance(f, io.BufferedRandom)
        with pytest.raises(
            BlockingError, match="method 'write' of '_io.BufferedRandom'"
        ):
            f.write(b"foo")
        with pytest.raises(
            BlockingError, match="method 'read' of '_io.BufferedRandom'"
        ):
            f.read(1)


async def test_file_read_bytes() -> None:
    with Path("/dev/null").open(mode="rb") as f:  # noqa: ASYNC230
        assert isinstance(f, io.BufferedReader)
        with pytest.raises(
            BlockingError, match="method 'read' of '_io.BufferedReader'"
        ):
            f.read(1)


async def test_file_write_bytes() -> None:
    with Path("/dev/null").open(mode="wb") as f:  # noqa: ASYNC230
        assert isinstance(f, io.BufferedWriter)
        with pytest.raises(
            BlockingError, match="method 'write' of '_io.BufferedWriter'"
        ):
            f.write(b"foo")


async def test_sqlite_connnection_execute() -> None:
    with (
        contextlib.closing(sqlite3.connect(":memory:")) as connection,
        pytest.raises(BlockingError, match="method 'execute' of 'sqlite3.Connection'"),
    ):
        connection.execute("SELECT 1")


async def test_sqlite_cursor_execute() -> None:
    with (
        contextlib.closing(sqlite3.connect(":memory:")) as connection,
        contextlib.closing(connection.cursor()) as cursor,
        pytest.raises(BlockingError, match="method 'execute' of 'sqlite3.Cursor'"),
    ):
        cursor.execute("SELECT 1")


async def test_import_module() -> None:
    importlib.reload(requests)


def allowed_read() -> None:
    with Path("/dev/null").open(mode="rb") as f:
        f.read(1)


async def test_custom_stack_exclude(blockbuster: BlockBuster) -> None:
    blockbuster.functions["io.BufferedReader.read"].can_block_functions.append(
        ("tests/test_blockbuster.py", {"allowed_read"})
    )
    allowed_read()


async def test_cleanup(blockbuster: BlockBuster) -> None:
    blockbuster.deactivate()
    with Path("/dev/null").open(mode="wb") as f:  # noqa: ASYNC230
        f.write(b"foo")
