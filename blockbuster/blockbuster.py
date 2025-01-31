"""BlockBuster module."""

from __future__ import annotations

import _thread
import asyncio
import builtins
import importlib
import inspect
import io
import logging
import os
import socket
import sqlite3
import ssl
import sys
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, List, TypeVar, Union

import forbiddenfruit

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable, Iterable, Iterator
    from types import ModuleType

    ModulesType = Union[str, ModuleType, List[Union[str, ModuleType]], None]


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

blockbuster_skip: ContextVar[bool] = ContextVar("blockbuster_skip")


def _wrap_blocking(
    modules: list[str],
    func: Callable[..., _T],
    can_block_functions: list[tuple[str, Iterable[str]]],
    can_block_predicate: Callable[..., bool],
) -> Callable[..., _T]:
    """Wrap blocking function."""

    def wrapper(*args: Any, **kwargs: Any) -> _T:
        if blockbuster_skip.get(False):
            return func(*args, **kwargs)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return func(*args, **kwargs)
        skip_token = blockbuster_skip.set(True)
        try:
            if can_block_predicate(*args, **kwargs):
                return func(*args, **kwargs)
            frame = inspect.currentframe()
            in_test_module = False
            while frame:
                frame_info = inspect.getframeinfo(frame)
                for module in modules:
                    if frame_info.filename.startswith(module):
                        in_test_module = True
                for filename, functions in can_block_functions:
                    if (
                        frame_info.filename.endswith(filename)
                        and frame_info.function in functions
                    ):
                        return func(*args, **kwargs)
                frame = frame.f_back
            if not modules or in_test_module:
                raise _blocking_error(func)
            return func(*args, **kwargs)
        finally:
            blockbuster_skip.reset(skip_token)

    return wrapper


class BlockBusterFunction:
    """BlockBusterFunction class."""

    def __init__(
        self,
        module: ModuleType | type,
        func_name: str,
        *,
        scanned_modules: ModulesType = None,
        can_block_functions: list[tuple[str, Iterable[str]]] | None = None,
        can_block_predicate: Callable[..., bool] = lambda *_, **__: False,
    ) -> None:
        """Create a BlockBusterFunction.

        Args:
            module: The module that contains the blocking function.
            func_name: The name of the blocking function.
            scanned_modules: The modules from which blocking calls are detected.
                If None, the blocking calls are detected from all the modules.
                Can be a module name, a module object, a list of module names or a
                list of module objects.
            can_block_functions: Optional functions in the stack where blocking is
                allowed.
            can_block_predicate: An optional predicate that determines if blocking is
                allowed.

        """
        self.module = module
        self.func_name = func_name
        self.original_func = getattr(module, func_name, None)
        self.can_block_functions: list[tuple[str, Iterable[str]]] = (
            can_block_functions or []
        )
        self.can_block_predicate: Callable[..., bool] = can_block_predicate
        self.activated = False
        self._scanned_modules: list[str] = []
        if isinstance(scanned_modules, list):
            _scanned_modules = scanned_modules
        elif isinstance(scanned_modules, str):
            _scanned_modules = [scanned_modules]
        else:
            _scanned_modules = []
        for scanned_module in _scanned_modules:
            if isinstance(scanned_module, str):
                module_ = importlib.import_module(scanned_module)
                if hasattr(module_, "__path__"):
                    self._scanned_modules.append(module_.__path__[0])
                elif file := module_.__file__:
                    self._scanned_modules.append(file)
                else:
                    logging.warning("Cannot get path for %s", scanned_module)

    def activate(self) -> BlockBusterFunction:
        """Activate the blocking detection."""
        if self.original_func is None or self.activated:
            return self
        self.activated = True
        checker = _wrap_blocking(
            self._scanned_modules,
            self.original_func,
            self.can_block_functions,
            self.can_block_predicate,
        )
        try:
            setattr(self.module, self.func_name, checker)
        except TypeError:
            forbiddenfruit.curse(self.module, self.func_name, checker)
        return self

    def deactivate(self) -> BlockBusterFunction:
        """Deactivate the blocking detection."""
        if self.original_func is None or not self.activated:
            return self
        self.activated = False
        try:
            setattr(self.module, self.func_name, self.original_func)
        except TypeError:
            forbiddenfruit.curse(self.module, self.func_name, self.original_func)
        return self

    def can_block_in(
        self, filename: str, functions: str | Iterable[str]
    ) -> BlockBusterFunction:
        """Add functions where it is allowed to block.

        Args:
            filename (str): The filename that contains the functions.
            functions (str | Iterable[str]): The functions where blocking is allowed.

        """
        if isinstance(functions, str):
            functions = {functions}
        self.can_block_functions.append((filename, functions))
        return self


def _get_time_wrapped_functions(
    modules: ModulesType = None,
) -> dict[str, BlockBusterFunction]:
    return {
        "time.sleep": BlockBusterFunction(
            time,
            "sleep",
            can_block_functions=[
                ("/pydevd.py", {"_do_wait_suspend"}),
            ],
            scanned_modules=modules,
        )
    }


def _get_os_wrapped_functions(
    modules: ModulesType = None,
) -> dict[str, BlockBusterFunction]:
    functions = {
        f"os.{method}": BlockBusterFunction(os, method, scanned_modules=modules)
        for method in (
            "getcwd",
            "statvfs",
            "sendfile",
            "rename",
            "remove",
            "unlink",
            "rmdir",
            "link",
            "symlink",
            "readlink",
            "listdir",
            "scandir",
            "access",
        )
    }

    functions["os.stat"] = BlockBusterFunction(
        os,
        "stat",
        can_block_functions=[
            ("<frozen importlib._bootstrap>", {"_find_and_load"}),
            ("linecache.py", {"checkcache", "updatecache"}),
            ("coverage/control.py", {"_should_trace"}),
        ],
        scanned_modules=modules,
    )

    functions["os.mkdir"] = BlockBusterFunction(
        os,
        "mkdir",
        can_block_functions=[("_pytest/assertion/rewrite.py", {"try_makedirs"})],
        scanned_modules=modules,
    )

    functions["os.replace"] = BlockBusterFunction(
        os,
        "replace",
        can_block_functions=[("_pytest/assertion/rewrite.py", {"_write_pyc"})],
        scanned_modules=modules,
    )

    for method in (
        "ismount",
        "samestat",
        "sameopenfile",
    ):
        functions[f"os.path.{method}"] = BlockBusterFunction(
            os.path, method, scanned_modules=modules
        )

    functions["os.path.islink"] = BlockBusterFunction(
        os.path,
        "islink",
        can_block_functions=[
            ("coverage/control.py", {"_should_trace"}),
            ("/pydevd_file_utils.py", {"get_abs_path_real_path_and_base_from_file"}),
        ],
        scanned_modules=modules,
    )

    functions["os.path.abspath"] = BlockBusterFunction(
        os.path,
        "abspath",
        can_block_functions=[
            ("_pytest/assertion/rewrite.py", {"_should_rewrite"}),
            ("coverage/control.py", {"_should_trace"}),
            ("/pydevd_file_utils.py", {"get_abs_path_real_path_and_base_from_file"}),
        ],
        scanned_modules=modules,
    )

    def os_rw_exclude(fd: int, *_: Any, **__: Any) -> bool:
        return not os.get_blocking(fd)

    functions["os.read"] = BlockBusterFunction(
        os,
        "read",
        can_block_predicate=os_rw_exclude,
        scanned_modules=modules,
    )
    functions["os.write"] = BlockBusterFunction(
        os,
        "write",
        can_block_predicate=os_rw_exclude,
        scanned_modules=modules,
    )

    return functions


def _get_io_wrapped_functions(
    modules: ModulesType = None,
) -> dict[str, BlockBusterFunction]:
    stdout = sys.stdout
    stderr = sys.stderr

    def file_write_exclude(file: io.IOBase, *_: Any, **__: Any) -> bool:
        return file in {stdout, stderr, sys.stdout, sys.stderr} or file.isatty()

    return {
        "io.BufferedReader.read": BlockBusterFunction(
            io.BufferedReader,
            "read",
            can_block_functions=[
                ("<frozen importlib._bootstrap_external>", {"get_data"}),
                ("_pytest/assertion/rewrite.py", {"_rewrite_test", "_read_pyc"}),
            ],
            scanned_modules=modules,
        ),
        "io.BufferedWriter.write": BlockBusterFunction(
            io.BufferedWriter,
            "write",
            can_block_functions=[("_pytest/assertion/rewrite.py", {"_write_pyc"})],
            can_block_predicate=file_write_exclude,
            scanned_modules=modules,
        ),
        "io.BufferedRandom.read": BlockBusterFunction(
            io.BufferedRandom, "read", scanned_modules=modules
        ),
        "io.BufferedRandom.write": BlockBusterFunction(
            io.BufferedRandom,
            "write",
            can_block_predicate=file_write_exclude,
            scanned_modules=modules,
        ),
        "io.TextIOWrapper.read": BlockBusterFunction(
            io.TextIOWrapper,
            "read",
            can_block_functions=[("aiofile/version.py", {"<module>"})],
            scanned_modules=modules,
        ),
        "io.TextIOWrapper.write": BlockBusterFunction(
            io.TextIOWrapper,
            "write",
            can_block_predicate=file_write_exclude,
            scanned_modules=modules,
        ),
    }


def _socket_exclude(sock: socket.socket, *_: Any, **__: Any) -> bool:
    return not sock.getblocking()


def _get_socket_wrapped_functions(
    modules: ModulesType = None,
) -> dict[str, BlockBusterFunction]:
    return {
        f"socket.socket.{method}": BlockBusterFunction(
            socket.socket,
            method,
            can_block_predicate=_socket_exclude,
            scanned_modules=modules,
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


def _get_ssl_wrapped_functions(
    modules: ModulesType = None,
) -> dict[str, BlockBusterFunction]:
    return {
        f"ssl.SSLSocket.{method}": BlockBusterFunction(
            ssl.SSLSocket,
            method,
            can_block_predicate=_socket_exclude,
            scanned_modules=modules,
        )
        for method in ("write", "send", "read", "recv")
    }


def _get_sqlite_wrapped_functions(
    modules: ModulesType = None,
) -> dict[str, BlockBusterFunction]:
    functions = {
        f"sqlite3.Cursor.{method}": BlockBusterFunction(
            sqlite3.Cursor, method, scanned_modules=modules
        )
        for method in (
            "execute",
            "executemany",
            "executescript",
            "fetchone",
            "fetchmany",
            "fetchall",
        )
    }

    for method in ("execute", "executemany", "executescript", "commit", "rollback"):
        functions[f"sqlite3.Connection.{method}"] = BlockBusterFunction(
            sqlite3.Connection,
            method,
            scanned_modules=modules,
        )

    return functions


def _get_lock_wrapped_functions(
    modules: ModulesType = None,
) -> dict[str, BlockBusterFunction]:
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
                ("threading.py", {"start"}),
                ("asyncio/base_events.py", {"shutdown_default_executor"}),
            ],
            scanned_modules=modules,
        ),
        "threading.Lock.acquire_lock": BlockBusterFunction(
            _thread.LockType,
            "acquire_lock",
            can_block_predicate=lock_acquire_exclude,
            can_block_functions=[("threading.py", {"start"})],
            scanned_modules=modules,
        ),
    }


def _get_builtins_wrapped_functions(
    modules: ModulesType = None,
) -> dict[str, BlockBusterFunction]:
    return {
        "builtins.input": BlockBusterFunction(
            builtins, "input", scanned_modules=modules
        )
    }


class BlockBuster:
    """BlockBuster class."""

    def __init__(self, scanned_modules: ModulesType = None) -> None:
        """Initialize BlockBuster.

        Args:
            scanned_modules: The modules from which blocking calls are detected.
                If None, the blocking calls are detected from all the modules.
                Can be a module name, a module object, a list of module names or a
                list of module objects.
        """
        self.functions = {
            **_get_time_wrapped_functions(scanned_modules),
            **_get_os_wrapped_functions(scanned_modules),
            **_get_io_wrapped_functions(scanned_modules),
            **_get_socket_wrapped_functions(scanned_modules),
            **_get_ssl_wrapped_functions(scanned_modules),
            **_get_sqlite_wrapped_functions(scanned_modules),
            **_get_lock_wrapped_functions(scanned_modules),
            **_get_builtins_wrapped_functions(scanned_modules),
        }

    def activate(self) -> None:
        """Activate all the functions."""
        for wrapped_function in self.functions.values():
            wrapped_function.activate()

    def deactivate(self) -> None:
        """Deactivate all the functions."""
        for wrapped_function in self.functions.values():
            wrapped_function.deactivate()


@contextmanager
def blockbuster_ctx(scanned_modules: ModulesType = None) -> Iterator[BlockBuster]:
    """Context manager for using BlockBuster.

    Args:
        scanned_modules: The modules from which blocking calls are detected.
            If None, the blocking calls are detected from all the modules.
            Can be a list of module names or module objects.
    """
    blockbuster = BlockBuster(scanned_modules)
    blockbuster.activate()
    yield blockbuster
    blockbuster.deactivate()
