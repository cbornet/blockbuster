import asyncio
import contextlib
import importlib
import io
import os
import re
import socket
import sqlite3
import sys
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
    tcp_server_task = asyncio.create_task(asyncio.to_thread(tcp_server))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        while True:
            with contextlib.suppress(ConnectionRefusedError):
                await asyncio.sleep(0.1)
                await asyncio.to_thread(s.connect, ("127.0.0.1", PORT))
                break
        with pytest.raises(BlockingError, match="method 'send' of '_socket.socket'"):
            s.send(b"Hello, world")
    await tcp_server_task


async def test_socket_send_non_blocking() -> None:
    tcp_server_task = asyncio.create_task(asyncio.to_thread(tcp_server))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        while True:
            with contextlib.suppress(ConnectionRefusedError):
                await asyncio.sleep(0.1)
                await asyncio.to_thread(s.connect, ("127.0.0.1", PORT))
                break
        s.setblocking(False)  # noqa: FBT003
        s.send(b"Hello, world")
    await tcp_server_task


async def test_ssl_socket(blockbuster: BlockBuster) -> None:
    blockbuster.functions["socket.socket.connect"].deactivate()
    blockbuster.functions["os.stat"].deactivate()
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


async def test_write_std() -> None:
    sys.stdout.write("test")
    sys.stderr.write("test")


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


async def test_lock() -> None:
    lock = threading.Lock()
    assert lock.acquire() is True
    with pytest.raises(BlockingError, match="method 'acquire' of '_thread.lock'"):
        lock.acquire()


async def test_lock_timeout_zero() -> None:
    lock = threading.Lock()
    assert lock.acquire() is True
    assert lock.acquire(timeout=0) is False


async def test_lock_non_blocking() -> None:
    lock = threading.Lock()
    assert lock.acquire() is True
    assert lock.acquire(blocking=False) is False


async def test_thread_start() -> None:
    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()


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


async def test_os_stat() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("stat (<module 'posix' (built-in)>")
    ):
        Path("/").stat()


async def test_os_getcwd() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("getcwd (<module 'posix' (built-in)>")
    ):
        Path.cwd()


@pytest.mark.skipif(not hasattr(os, "statvfs"), reason="statvfs is not available")
async def test_os_statvfs() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("statvfs (<module 'posix' (built-in)>")
    ):
        os.statvfs("/")


@pytest.mark.skipif(not hasattr(os, "sendfile"), reason="sendfile is not available")
async def test_os_sendfile() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("sendfile (<module 'posix' (built-in)>")
    ):
        os.sendfile(0, 1, 0, 1)


async def test_os_rename() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("rename (<module 'posix' (built-in)>")
    ):
        Path("/1").rename("/2")


async def test_os_renames() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("stat (<module 'posix' (built-in)>")
    ):
        os.renames("/1", "/2")


async def test_os_replace() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("replace (<module 'posix' (built-in)>")
    ):
        Path("/1").replace("/2")


async def test_os_unlink() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("unlink (<module 'posix' (built-in)>")
    ):
        Path("/1").unlink()


async def test_os_mkdir() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("mkdir (<module 'posix' (built-in)>")
    ):
        Path("/1").mkdir()


async def test_os_makedirs() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("stat (<module 'posix' (built-in)>")
    ):
        os.makedirs("/1")  # noqa: PTH103


async def test_os_rmdir() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("rmdir (<module 'posix' (built-in)>")
    ):
        Path("/1").rmdir()


async def test_os_removedirs() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("rmdir (<module 'posix' (built-in)>")
    ):
        os.removedirs("/1")


async def test_os_link() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("link (<module 'posix' (built-in)>")
    ):
        os.link("/1", "/2")


async def test_os_symlink() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("symlink (<module 'posix' (built-in)>")
    ):
        os.symlink("/1", "/2")


async def test_os_readlink() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("readlink (<module 'posix' (built-in)>")
    ):
        os.readlink("/1")


async def test_os_listdir() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("listdir (<module 'posix' (built-in)>")
    ):
        os.listdir("/1")


async def test_os_scandir() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("scandir (<module 'posix' (built-in)>")
    ):
        os.scandir("/1")


async def test_os_access() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("access (<module 'posix' (built-in)>")
    ):
        os.access("/1", os.F_OK)


async def test_builtins_input() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("input (<module 'builtins' (built-in)>")
    ):
        input()
