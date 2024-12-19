import asyncio
import contextlib
import contextvars
import functools
import importlib
import io
import os
import re
import socket
import sqlite3
import sys
import threading
import time
from asyncio import events
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

import pytest
import requests

from blockbuster import BlockBuster, BlockingError, blockbuster_ctx

_T = TypeVar("_T")


async def to_thread(func: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
    loop = events.get_running_loop()
    ctx = contextvars.copy_context()
    func_call = functools.partial(ctx.run, func, *args, **kwargs)
    return await loop.run_in_executor(None, func_call)


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
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s, pytest.raises(
        BlockingError, match="method 'connect' of '_socket.socket'"
    ):
        s.connect(("127.0.0.1", PORT))


async def test_socket_send() -> None:
    tcp_server_task = asyncio.create_task(to_thread(tcp_server))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        while True:
            with contextlib.suppress(ConnectionRefusedError):
                await asyncio.sleep(0.1)
                await to_thread(s.connect, ("127.0.0.1", PORT))
                break
        with pytest.raises(BlockingError, match="method 'send' of '_socket.socket'"):
            s.send(b"Hello, world")
    await tcp_server_task


async def test_socket_send_non_blocking() -> None:
    tcp_server_task = asyncio.create_task(to_thread(tcp_server))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        while True:
            with contextlib.suppress(ConnectionRefusedError):
                await asyncio.sleep(0.1)
                await to_thread(s.connect, ("127.0.0.1", PORT))
                break
        blocking = False
        s.setblocking(blocking)
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
    with contextlib.closing(sqlite3.connect(":memory:")) as connection, pytest.raises(
        BlockingError, match="method 'execute' of 'sqlite3.Connection'"
    ):
        connection.execute("SELECT 1")


async def test_sqlite_cursor_execute() -> None:
    with contextlib.closing(
        sqlite3.connect(":memory:")
    ) as connection, contextlib.closing(connection.cursor()) as cursor, pytest.raises(
        BlockingError, match="method 'execute' of 'sqlite3.Cursor'"
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
    with pytest.raises(BlockingError, match=r"read \(<module '.*' \(built-in\)>\)"):
        os.read(fd, 1)


async def test_os_read_non_blocking() -> None:
    fd = os.open("/dev/null", os.O_NONBLOCK | os.O_RDONLY)
    os.read(fd, 1)


async def test_os_write() -> None:
    fd = os.open("/dev/null", os.O_RDWR)
    with pytest.raises(BlockingError, match=r"write \(<module '.*' \(built-in\)>\)"):
        os.write(fd, b"foo")


async def test_os_write_non_blocking() -> None:
    fd = os.open("/dev/null", os.O_NONBLOCK | os.O_RDWR)
    os.write(fd, b"foo")


async def test_os_stat() -> None:
    with pytest.raises(BlockingError, match=r"stat \(<module '.*' \(built-in\)>\)"):
        os.stat("/1")


async def test_os_getcwd() -> None:
    with pytest.raises(BlockingError, match=r"getcwd \(<module '.*' \(built-in\)>\)"):
        os.getcwd()


@pytest.mark.skipif(not hasattr(os, "statvfs"), reason="statvfs is not available")
async def test_os_statvfs() -> None:
    with pytest.raises(BlockingError, match=r"statvfs \(<module '.*' \(built-in\)>\)"):
        os.statvfs("/")


@pytest.mark.skipif(not hasattr(os, "sendfile"), reason="sendfile is not available")
async def test_os_sendfile() -> None:
    with pytest.raises(BlockingError, match=r"sendfile \(<module '.*' \(built-in\)>\)"):
        os.sendfile(0, 1, 0, 1)


async def test_os_rename() -> None:
    with pytest.raises(BlockingError, match=r"rename \(<module '.*' \(built-in\)>\)"):
        os.rename("/1", "/2")


async def test_os_renames() -> None:
    with pytest.raises(BlockingError, match=r"stat \(<module '.*' \(built-in\)>\)"):
        os.renames("/1", "/2")


async def test_os_replace() -> None:
    with pytest.raises(BlockingError, match=r"replace \(<module '.*' \(built-in\)>\)"):
        os.replace("/1", "/2")


async def test_os_unlink() -> None:
    with pytest.raises(BlockingError, match=r"unlink \(<module '.*' \(built-in\)>\)"):
        os.unlink("/1")


async def test_os_mkdir() -> None:
    with pytest.raises(BlockingError, match=r"mkdir \(<module '.*' \(built-in\)>\)"):
        os.mkdir("/1")


async def test_os_makedirs() -> None:
    with pytest.raises(BlockingError, match=r"stat \(<module '.*' \(built-in\)>\)"):
        os.makedirs("/1")


async def test_os_rmdir() -> None:
    with pytest.raises(BlockingError, match=r"rmdir \(<module '.*' \(built-in\)>\)"):
        os.rmdir("/1")


async def test_os_removedirs() -> None:
    with pytest.raises(BlockingError, match=r"rmdir \(<module '.*' \(built-in\)>\)"):
        os.removedirs("/1")


async def test_os_link() -> None:
    with pytest.raises(BlockingError, match=r"link \(<module '.*' \(built-in\)>\)"):
        os.link("/1", "/2")


async def test_os_symlink() -> None:
    with pytest.raises(BlockingError, match=r"symlink \(<module '.*' \(built-in\)>\)"):
        os.symlink("/1", "/2")


async def test_os_readlink() -> None:
    with pytest.raises(BlockingError, match=r"readlink \(<module '.*' \(built-in\)>\)"):
        os.readlink("/1")


async def test_os_listdir() -> None:
    with pytest.raises(BlockingError, match=r"listdir \(<module '.*' \(built-in\)>\)"):
        os.listdir("/1")


async def test_os_scandir() -> None:
    with pytest.raises(BlockingError, match=r"scandir \(<module '.*' \(built-in\)>\)"):
        os.scandir("/1")


async def test_os_access() -> None:
    with pytest.raises(BlockingError, match=r"access \(<module '.*' \(built-in\)>\)"):
        os.access("/1", os.F_OK)


async def test_os_path_exists() -> None:
    with pytest.raises(BlockingError, match=r"stat \(<module '.*' \(built-in\)>\)"):
        os.path.exists("/1")


async def test_os_path_isfile() -> None:
    with pytest.raises(BlockingError, match=r"stat \(<module '.*' \(built-in\)>\)"):
        os.path.isfile("/1")


async def test_os_path_isdir() -> None:
    with pytest.raises(BlockingError, match=r"stat \(<module '.*' \(built-in\)>\)"):
        os.path.isdir("/1")


async def test_os_path_islink() -> None:
    with pytest.raises(BlockingError, match="path.islink"):
        os.path.islink("/1")


async def test_os_path_ismount() -> None:
    with pytest.raises(BlockingError, match="path.ismount"):
        os.path.ismount("/1")


async def test_os_path_getsize() -> None:
    with pytest.raises(BlockingError, match=r"stat \(<module '.*' \(built-in\)>\)"):
        os.path.getsize("/1")


async def test_os_path_getmtime() -> None:
    with pytest.raises(BlockingError, match=r"stat \(<module '.*' \(built-in\)>\)"):
        os.path.getmtime("/1")


async def test_os_path_getatime() -> None:
    with pytest.raises(BlockingError, match=r"stat \(<module '.*' \(built-in\)>\)"):
        os.path.getatime("/1")


async def test_os_path_getctime() -> None:
    with pytest.raises(BlockingError, match=r"stat \(<module '.*' \(built-in\)>\)"):
        os.path.getctime("/1")


async def test_os_path_samefile() -> None:
    with pytest.raises(BlockingError, match=r"stat \(<module '.*' \(built-in\)>\)"):
        os.path.samefile("/1", "/2")


async def test_os_path_sameopenfile() -> None:
    with pytest.raises(BlockingError, match="path.sameopenfile"):
        os.path.sameopenfile(0, 0)


async def test_os_path_samestat(blockbuster: BlockBuster) -> None:
    blockbuster.functions["os.stat"].deactivate()
    with pytest.raises(BlockingError, match="path.samestat"):
        os.path.samestat(os.stat(0), os.stat(0))


async def test_os_path_abspath() -> None:
    with pytest.raises(BlockingError, match="path.abspath"):
        os.path.abspath("/1")


async def test_builtins_input() -> None:
    with pytest.raises(
        BlockingError, match=re.escape("input (<module 'builtins' (built-in)>")
    ):
        input()


def test_can_block_in_builder(blockbuster: BlockBuster) -> None:
    blockbuster.functions["os.stat"].can_block_in("foo.py", {"bar"}).can_block_in(
        "baz.py", "qux"
    )
    assert ("foo.py", {"bar"}) in blockbuster.functions["os.stat"].can_block_functions
    assert ("baz.py", {"qux"}) in blockbuster.functions["os.stat"].can_block_functions
