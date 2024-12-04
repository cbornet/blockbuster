"""BlockBuster module."""

from __future__ import annotations

import _thread
import asyncio
import inspect
import io
import os
import socket
import sqlite3
import ssl
import sys
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, TypeVar

import forbiddenfruit

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable, Iterable, Iterator
    from types import ModuleType


class BlockingError(Exception):
    """BlockingError class."""


def _blocking_error(func: Callable[..., Any]) -> BlockingError:
    if inspect.isbuiltin(func):
        msg = f"Blocking call to {func.__qualname__} ({func.__self__})"
    elif inspect.ismethoddescriptor(func):
        msg = f"Blocking call to {func}"
    else:
        msg = f"Blocking call to {func.__module__}.{func.__qualname__}"
    return BlockingError(msg)


_T = TypeVar("_T")


def _wrap_blocking(
    func: Callable[..., _T],
    can_block_functions: list[tuple[str, Iterable[str]]],
    can_block_predicate: Callable[..., bool],
) -> Callable[..., _T]:
    """Wrap blocking function."""

    def wrapper(*args: Any, **kwargs: Any) -> _T:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return func(*args, **kwargs)
        for filename, functions in can_block_functions:
            for frame_info in inspect.stack():
                if (
                    frame_info.filename.endswith(filename)
                    and frame_info.function in functions
                ):
                    return func(*args, **kwargs)
        if can_block_predicate(*args, **kwargs):
            return func(*args, **kwargs)
        raise _blocking_error(func)

    return wrapper


class BlockBusterFunction:
    """BlockBusterFunction class."""

    def __init__(
        self,
        module: ModuleType | type,
        func_name: str,
        *,
        can_block_functions: list[tuple[str, Iterable[str]]] | None = None,
        can_block_predicate: Callable[..., bool] = lambda *_, **__: False,
    ) -> None:
        """Initialize BlockBusterFunction."""
        self.module = module
        self.func_name = func_name
        self.original_func = getattr(module, func_name)
        self.can_block_functions: list[tuple[str, Iterable[str]]] = (
            can_block_functions or []
        )
        self.can_block_predicate: Callable[..., bool] = can_block_predicate
        self.activated = False

    def activate(self) -> None:
        """Activate the blocking detection."""
        if self.activated:
            return
        self.activated = True
        checker = _wrap_blocking(
            self.original_func, self.can_block_functions, self.can_block_predicate
        )
        try:
            setattr(self.module, self.func_name, checker)
        except TypeError:
            forbiddenfruit.curse(self.module, self.func_name, checker)

    def deactivate(self) -> None:
        """Deactivate the blocking detection."""
        if not self.activated:
            return
        self.activated = False
        try:
            setattr(self.module, self.func_name, self.original_func)
        except TypeError:
            forbiddenfruit.curse(self.module, self.func_name, self.original_func)


def _get_time_wrapped_functions() -> dict[str, BlockBusterFunction]:
    return {
        "time.sleep": BlockBusterFunction(
            time,
            "sleep",
            can_block_functions=[
                ("pydev/pydevd.py", {"_do_wait_suspend"}),
                ("pydevd/pydevd.py", {"_do_wait_suspend"}),
            ],
        )
    }


def _get_os_wrapped_functions() -> dict[str, BlockBusterFunction]:
    def os_exclude(fd: int, *_: Any, **__: Any) -> bool:
        return not os.get_blocking(fd)

    return {
        "os.read": BlockBusterFunction(os, "read", can_block_predicate=os_exclude),
        "os.write": BlockBusterFunction(os, "write", can_block_predicate=os_exclude),
    }


def _get_io_wrapped_functions() -> dict[str, BlockBusterFunction]:
    def file_write_exclude(file: io.IOBase, *_: Any, **__: Any) -> bool:
        return file in {sys.stdout, sys.stderr}

    return {
        "io.BufferedReader.read": BlockBusterFunction(
            io.BufferedReader,
            "read",
            can_block_functions=[
                ("<frozen importlib._bootstrap_external>", {"get_data"}),
                ("_pytest/assertion/rewrite.py", {"_rewrite_test", "_read_pyc"}),
                ("aiofile/version.py", {"<module>"}),
            ],
        ),
        "io.BufferedWriter.write": BlockBusterFunction(
            io.BufferedWriter,
            "write",
            can_block_functions=[("_pytest/assertion/rewrite.py", {"_write_pyc"})],
            can_block_predicate=file_write_exclude,
        ),
        "io.BufferedRandom.read": BlockBusterFunction(io.BufferedRandom, "read"),
        "io.BufferedRandom.write": BlockBusterFunction(
            io.BufferedRandom,
            "write",
            can_block_predicate=file_write_exclude,
        ),
        "io.TextIOWrapper.read": BlockBusterFunction(
            io.TextIOWrapper,
            "read",
            can_block_functions=[("aiofile/version.py", {"<module>"})],
        ),
        "io.TextIOWrapper.write": BlockBusterFunction(
            io.TextIOWrapper,
            "write",
            can_block_predicate=file_write_exclude,
        ),
    }


def _socket_exclude(sock: socket.socket, *_: Any, **__: Any) -> bool:
    return not sock.getblocking()


def _get_socket_wrapped_functions() -> dict[str, BlockBusterFunction]:
    return {
        f"socket.socket.{method}": BlockBusterFunction(
            socket.socket, method, can_block_predicate=_socket_exclude
        )
        for method in (
            "connect",
            "accept",
            "send",
            "sendall",
            "sendto",
            "recv",
            "recv_into",
            "recvfrom",
            "recvfrom_into",
            "recvmsg",
        )
    }


def _get_ssl_wrapped_functions() -> dict[str, BlockBusterFunction]:
    return {
        f"ssl.SSLSocket.{method}": BlockBusterFunction(
            ssl.SSLSocket, method, can_block_predicate=_socket_exclude
        )
        for method in ("write", "send", "read", "recv")
    }


def _get_sqlite_wrapped_functions() -> dict[str, BlockBusterFunction]:
    return {
        f"sqlite3.Cursor.{method}": BlockBusterFunction(sqlite3.Cursor, method)
        for method in (
            "execute",
            "executemany",
            "executescript",
            "fetchone",
            "fetchmany",
            "fetchall",
        )
    } | {
        f"sqlite3.Connection.{method}": BlockBusterFunction(sqlite3.Connection, method)
        for method in ("execute", "executemany", "executescript", "commit", "rollback")
    }


def _get_lock_wrapped_functions() -> dict[str, BlockBusterFunction]:
    def lock_acquire_exclude(
        lock: threading.Lock,
        blocking: bool = True,  # noqa: FBT001, FBT002
        timeout: int = -1,
    ) -> bool:
        return not blocking or timeout == 0 or not lock.locked()

    return {
        "threading.Lock.acquire": BlockBusterFunction(
            _thread.LockType,
            "acquire",
            can_block_predicate=lock_acquire_exclude,
            can_block_functions=[
                ("concurrent/futures/thread.py", {"_adjust_thread_count"})
            ],
        ),
        "threading.Lock.acquire_lock": BlockBusterFunction(
            _thread.LockType,
            "acquire_lock",
            can_block_predicate=lock_acquire_exclude,
            can_block_functions=[
                ("concurrent/futures/thread.py", {"_adjust_thread_count"})
            ],
        ),
    }


class BlockBuster:
    """BlockBuster class."""

    def __init__(self) -> None:
        """Initialize BlockBuster."""
        self.functions = (
            _get_time_wrapped_functions()
            | _get_os_wrapped_functions()
            | _get_io_wrapped_functions()
            | _get_socket_wrapped_functions()
            | _get_ssl_wrapped_functions()
            | _get_sqlite_wrapped_functions()
            | _get_lock_wrapped_functions()
        )

    def activate(self) -> None:
        """Activate all the functions."""
        for wrapped_function in self.functions.values():
            wrapped_function.activate()

    def deactivate(self) -> None:
        """Deactivate all the functions."""
        for wrapped_function in self.functions.values():
            wrapped_function.deactivate()


@contextmanager
def blockbuster_ctx() -> Iterator[BlockBuster]:
    """Context manager for using BlockBuster."""
    blockbuster = BlockBuster()
    blockbuster.activate()
    yield blockbuster
    blockbuster.deactivate()
