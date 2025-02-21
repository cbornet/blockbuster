"""BlockBuster module."""

from __future__ import annotations

import _thread
import asyncio
import importlib
import inspect
import io
import logging
import os
import platform
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, List, TypeVar, Union

if TYPE_CHECKING:
    import socket
    import threading
    from collections.abc import Callable, Iterable, Iterator

    _ModuleList = Union[List[Union[str, ModuleType]], None]
    _ModuleOrModuleList = Union[str, ModuleType, _ModuleList]

if platform.python_implementation() == "CPython":
    import forbiddenfruit

    HAS_FORBIDDENFRUIT = True
else:
    HAS_FORBIDDENFRUIT = False


class BlockingError(Exception):
    """BlockingError class."""

    def __init__(self, func: str) -> None:
        """Initialize BlockingError.

        Args:
            func: The blocking function.

        """
        super().__init__(f"Blocking call to {func}")


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
    excluded_modules: list[str],
    func: Callable[..., _T],
    func_name: str,
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
                if not in_test_module:
                    in_excluded_module = False
                    for excluded_module in excluded_modules:
                        if frame_info.filename.startswith(excluded_module):
                            in_excluded_module = True
                            break
                    if not in_excluded_module:
                        for module in modules:
                            if frame_info.filename.startswith(module):
                                in_test_module = True
                                break
                frame_file_name = Path(frame_info.filename).as_posix()
                for filename, functions in can_block_functions:
                    if (
                        frame_file_name.endswith(filename)
                        and frame_info.function in functions
                    ):
                        return func(*args, **kwargs)
                frame = frame.f_back
            if not modules or in_test_module:
                raise BlockingError(func_name)
            return func(*args, **kwargs)
        finally:
            blockbuster_skip.reset(skip_token)

    return wrapper


def _resolve_module_paths(modules: list[str | ModuleType]) -> list[str]:
    resolved: list[str] = []
    for module in modules:
        module_ = importlib.import_module(module) if isinstance(module, str) else module
        if hasattr(module_, "__path__"):
            resolved.append(module_.__path__[0])
        elif file := module_.__file__:
            resolved.append(file)
        else:
            logging.warning("Cannot get path for %s", module_)
    return resolved


class BlockBusterFunction:
    """BlockBusterFunction class."""

    def __init__(
        self,
        module: ModuleType | type | None,
        func_name: str,
        *,
        scanned_modules: _ModuleOrModuleList = None,
        excluded_modules: _ModuleList = None,
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
            excluded_modules: Sub-modules that are excluded from scanned modules.
                It doesn't mean that blocking is allowed in these modules.
                It means that they are not considered as scanned modules.
                It is helpful for instance if the tests use a pytest plugin that is
                part of the scanned modules.
                Can be a list of module names or module objects.
            can_block_functions: Optional functions in the stack where blocking is
                allowed.
            can_block_predicate: An optional predicate that determines if blocking is
                allowed.

        """
        if module:
            self.module = module
            self.func_name = func_name
            self.original_func = getattr(module, func_name, None)
        else:
            tokens = func_name.split(".")
            if len(tokens) < 2:  # noqa: PLR2004
                msg = "module is required if func_name does not contain '.'"
                raise ValueError(msg)
            self.module = importlib.import_module(tokens.pop(0))
            while len(tokens) > 1:
                self.module = getattr(self.module, tokens.pop(0))
            self.func_name = tokens[0]
        self.original_func = getattr(self.module, self.func_name, None)
        if module is None:
            self.full_name = func_name
        else:
            self.full_name = f"{module.__name__}.{func_name}"
        self.can_block_functions: list[tuple[str, Iterable[str]]] = (
            can_block_functions or []
        )
        self.can_block_predicate: Callable[..., bool] = can_block_predicate
        self.activated = False
        self._scanned_modules: list[str] = []
        if isinstance(scanned_modules, list):
            _scanned_modules = scanned_modules
        elif isinstance(scanned_modules, (str, ModuleType)):
            _scanned_modules = [scanned_modules]
        else:
            _scanned_modules = []
        self._scanned_modules = _resolve_module_paths(_scanned_modules)
        self._excluded_modules: list[str] = _resolve_module_paths(
            excluded_modules or []
        )

    def activate(self) -> BlockBusterFunction:
        """Activate the blocking detection."""
        if self.original_func is None or self.activated:
            return self
        self.activated = True
        checker = _wrap_blocking(
            self._scanned_modules,
            self._excluded_modules,
            self.original_func,
            self.full_name,
            self.can_block_functions,
            self.can_block_predicate,
        )
        try:
            setattr(self.module, self.func_name, checker)
        except TypeError:
            if HAS_FORBIDDENFRUIT:
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
            if HAS_FORBIDDENFRUIT:
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
    modules: _ModuleOrModuleList = None, excluded_modules: _ModuleList = None
) -> dict[str, BlockBusterFunction]:
    return {
        "time.sleep": BlockBusterFunction(
            None,
            "time.sleep",
            can_block_functions=[
                ("/pydevd.py", {"_do_wait_suspend"}),
            ],
            scanned_modules=modules,
            excluded_modules=excluded_modules,
        )
    }


def _get_os_wrapped_functions(
    modules: _ModuleOrModuleList = None, excluded_modules: _ModuleList = None
) -> dict[str, BlockBusterFunction]:
    functions = {
        f"os.{method}": BlockBusterFunction(
            None,
            f"os.{method}",
            scanned_modules=modules,
            excluded_modules=excluded_modules,
        )
        for method in (
            "statvfs",
            "rename",
            "remove",
            "rmdir",
            "link",
            "symlink",
            "listdir",
            "access",
        )
    }

    functions["os.getcwd"] = BlockBusterFunction(
        None,
        "os.getcwd",
        can_block_functions=[
            ("coverage/control.py", {"_should_trace"}),
        ],
        scanned_modules=modules,
        excluded_modules=excluded_modules,
    )

    functions["os.stat"] = BlockBusterFunction(
        None,
        "os.stat",
        can_block_functions=[
            ("<frozen importlib._bootstrap>", {"_find_and_load"}),
            ("linecache.py", {"checkcache", "updatecache"}),
            ("coverage/control.py", {"_should_trace"}),
            ("asyncio/unix_events.py", {"create_unix_server", "_stop_serving"}),
        ],
        scanned_modules=modules,
        excluded_modules=excluded_modules,
    )

    functions["os.mkdir"] = BlockBusterFunction(
        None,
        "os.mkdir",
        can_block_functions=[("_pytest/assertion/rewrite.py", {"try_makedirs"})],
        scanned_modules=modules,
        excluded_modules=excluded_modules,
    )

    functions["os.replace"] = BlockBusterFunction(
        None,
        "os.replace",
        can_block_functions=[("_pytest/assertion/rewrite.py", {"_write_pyc"})],
        scanned_modules=modules,
        excluded_modules=excluded_modules,
    )

    functions["os.readlink"] = BlockBusterFunction(
        None,
        "os.readlink",
        can_block_functions=[
            ("coverage/control.py", {"_should_trace"}),
        ],
        scanned_modules=modules,
        excluded_modules=excluded_modules,
    )

    functions["os.sendfile"] = BlockBusterFunction(
        None,
        "os.sendfile",
        can_block_functions=[
            ("asyncio/base_events.py", {"sendfile"}),
        ],
        scanned_modules=modules,
        excluded_modules=excluded_modules,
    )

    functions["os.unlink"] = BlockBusterFunction(
        None,
        "os.unlink",
        can_block_functions=[
            ("asyncio/unix_events.py", {"_stop_serving"}),
        ],
        scanned_modules=modules,
        excluded_modules=excluded_modules,
    )

    if platform.python_implementation() != "CPython" or sys.version_info >= (3, 9):
        with os.scandir() as scandir_it:
            functions["os.scandir"] = BlockBusterFunction(
                type(scandir_it),
                "__next__",
                scanned_modules=modules,
                excluded_modules=excluded_modules,
            )

    for method in (
        "ismount",
        "samestat",
        "sameopenfile",
    ):
        functions[f"os.path.{method}"] = BlockBusterFunction(
            None,
            f"os.path.{method}",
            scanned_modules=modules,
            excluded_modules=excluded_modules,
        )

    functions["os.path.islink"] = BlockBusterFunction(
        None,
        "os.path.islink",
        can_block_functions=[
            ("coverage/control.py", {"_should_trace"}),
            ("/pydevd_file_utils.py", {"get_abs_path_real_path_and_base_from_file"}),
        ],
        scanned_modules=modules,
        excluded_modules=excluded_modules,
    )

    functions["os.path.abspath"] = BlockBusterFunction(
        None,
        "os.path.abspath",
        can_block_functions=[
            ("_pytest/assertion/rewrite.py", {"_should_rewrite"}),
            ("coverage/control.py", {"_should_trace"}),
            ("/pydevd_file_utils.py", {"get_abs_path_real_path_and_base_from_file"}),
        ],
        scanned_modules=modules,
        excluded_modules=excluded_modules,
    )

    def os_rw_exclude(fd: int, *_: Any, **__: Any) -> bool:
        return hasattr(os, "get_blocking") and not os.get_blocking(fd)

    os_rw_kwargs = (
        {} if platform.system() == "Windows" else {"can_block_predicate": os_rw_exclude}
    )

    functions["os.read"] = BlockBusterFunction(
        None,
        "os.read",
        can_block_functions=[
            ("asyncio/base_events.py", {"subprocess_exec"}),
            ("asyncio/base_events.py", {"subprocess_shell"}),
        ],
        scanned_modules=modules,
        excluded_modules=excluded_modules,
        **os_rw_kwargs,
    )
    functions["os.write"] = BlockBusterFunction(
        None,
        "os.write",
        can_block_functions=None,
        scanned_modules=modules,
        excluded_modules=excluded_modules,
        **os_rw_kwargs,
    )

    return functions


def _get_io_wrapped_functions(
    modules: _ModuleOrModuleList = None, excluded_modules: _ModuleList = None
) -> dict[str, BlockBusterFunction]:
    stdout = sys.stdout
    stderr = sys.stderr

    def file_read_exclude(file: io.IOBase, *_: Any, **__: Any) -> bool:
        try:
            file.fileno()
        except io.UnsupportedOperation:
            return not file.isatty()
        return False

    def file_write_exclude(file: io.IOBase, *_: Any, **__: Any) -> bool:
        if file in {stdout, stderr, sys.stdout, sys.stderr} or file.isatty():
            return True
        try:
            file.fileno()
        except io.UnsupportedOperation:
            return True
        return False

    return {
        "io.BufferedReader.read": BlockBusterFunction(
            None,
            "io.BufferedReader.read",
            can_block_functions=[
                ("<frozen importlib._bootstrap_external>", {"get_data"}),
                ("_pytest/assertion/rewrite.py", {"_rewrite_test", "_read_pyc"}),
            ],
            can_block_predicate=file_read_exclude,
            scanned_modules=modules,
            excluded_modules=excluded_modules,
        ),
        "io.BufferedWriter.write": BlockBusterFunction(
            None,
            "io.BufferedWriter.write",
            can_block_functions=[("_pytest/assertion/rewrite.py", {"_write_pyc"})],
            can_block_predicate=file_write_exclude,
            scanned_modules=modules,
            excluded_modules=excluded_modules,
        ),
        "io.BufferedRandom.read": BlockBusterFunction(
            None,
            "io.BufferedRandom.read",
            can_block_predicate=file_read_exclude,
            scanned_modules=modules,
            excluded_modules=excluded_modules,
        ),
        "io.BufferedRandom.write": BlockBusterFunction(
            None,
            "io.BufferedRandom.write",
            can_block_predicate=file_write_exclude,
            scanned_modules=modules,
            excluded_modules=excluded_modules,
        ),
        "io.TextIOWrapper.read": BlockBusterFunction(
            None,
            "io.TextIOWrapper.read",
            can_block_functions=[("aiofile/version.py", {"<module>"})],
            can_block_predicate=file_read_exclude,
            scanned_modules=modules,
            excluded_modules=excluded_modules,
        ),
        "io.TextIOWrapper.write": BlockBusterFunction(
            None,
            "io.TextIOWrapper.write",
            can_block_predicate=file_write_exclude,
            scanned_modules=modules,
            excluded_modules=excluded_modules,
        ),
    }


def _socket_exclude(sock: socket.socket, *_: Any, **__: Any) -> bool:
    return not sock.getblocking()


def _get_socket_wrapped_functions(
    modules: _ModuleOrModuleList = None, excluded_modules: _ModuleList = None
) -> dict[str, BlockBusterFunction]:
    return {
        f"socket.socket.{method}": BlockBusterFunction(
            None,
            f"socket.socket.{method}",
            can_block_predicate=_socket_exclude,
            scanned_modules=modules,
            excluded_modules=excluded_modules,
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
    modules: _ModuleOrModuleList = None, excluded_modules: _ModuleList = None
) -> dict[str, BlockBusterFunction]:
    return {
        f"ssl.SSLSocket.{method}": BlockBusterFunction(
            None,
            f"ssl.SSLSocket.{method}",
            can_block_predicate=_socket_exclude,
            scanned_modules=modules,
            excluded_modules=excluded_modules,
        )
        for method in ("write", "send", "read", "recv")
    }


def _get_sqlite_wrapped_functions(
    modules: _ModuleOrModuleList = None, excluded_modules: _ModuleList = None
) -> dict[str, BlockBusterFunction]:
    functions = {
        f"sqlite3.Cursor.{method}": BlockBusterFunction(
            None,
            f"sqlite3.Cursor.{method}",
            scanned_modules=modules,
            excluded_modules=excluded_modules,
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
            None,
            f"sqlite3.Connection.{method}",
            scanned_modules=modules,
            excluded_modules=excluded_modules,
        )

    return functions


def _get_lock_wrapped_functions(
    modules: _ModuleOrModuleList = None, excluded_modules: _ModuleList = None
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
                ("/pydevd.py", {"_do_wait_suspend"}),
                ("asyncio/base_events.py", {"shutdown_default_executor"}),
            ],
            scanned_modules=modules,
            excluded_modules=excluded_modules,
        ),
        "threading.Lock.acquire_lock": BlockBusterFunction(
            _thread.LockType,
            "acquire_lock",
            can_block_predicate=lock_acquire_exclude,
            can_block_functions=[("threading.py", {"start"})],
            scanned_modules=modules,
            excluded_modules=excluded_modules,
        ),
    }


def _get_builtins_wrapped_functions(
    modules: _ModuleOrModuleList = None, excluded_modules: _ModuleList = None
) -> dict[str, BlockBusterFunction]:
    return {
        "builtins.input": BlockBusterFunction(
            None,
            "builtins.input",
            scanned_modules=modules,
            excluded_modules=excluded_modules,
        )
    }


class BlockBuster:
    """BlockBuster class."""

    def __init__(
        self,
        scanned_modules: _ModuleOrModuleList = None,
        *,
        excluded_modules: _ModuleList = None,
    ) -> None:
        """Initialize BlockBuster.

        Args:
            scanned_modules: The modules from which blocking calls are detected.
                If None, the blocking calls are detected from all the modules.
                Can be a module name, a module object, a list of module names or a
                list of module objects.
            excluded_modules: Sub-modules that are excluded from scanned modules.
                It doesn't mean that blocking is allowed in these modules.
                It means that they are not considered as scanned modules.
                It is helpful for instance if the tests use a pytest plugin that is
                part of the scanned modules.
                Can be a list of module names or module objects.
        """
        self.functions = {
            **_get_time_wrapped_functions(scanned_modules, excluded_modules),
            **_get_os_wrapped_functions(scanned_modules, excluded_modules),
            **_get_io_wrapped_functions(scanned_modules, excluded_modules),
            **_get_socket_wrapped_functions(scanned_modules, excluded_modules),
            **_get_ssl_wrapped_functions(scanned_modules, excluded_modules),
            **_get_sqlite_wrapped_functions(scanned_modules, excluded_modules),
            **_get_lock_wrapped_functions(scanned_modules, excluded_modules),
            **_get_builtins_wrapped_functions(scanned_modules, excluded_modules),
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
def blockbuster_ctx(
    scanned_modules: _ModuleOrModuleList = None, *, excluded_modules: _ModuleList = None
) -> Iterator[BlockBuster]:
    """Context manager for using BlockBuster.

    Args:
        scanned_modules: The modules from which blocking calls are detected.
            If None, the blocking calls are detected from all the modules.
            Can be a list of module names or module objects.
        excluded_modules: Sub-modules that are excluded from scanned modules.
            It doesn't mean that blocking is allowed in these modules.
            It means that they are not considered as scanned modules.
            It is helpful for instance if the tests use a pytest plugin that is
            part of the scanned modules.
            Can be a list of module names or module objects.
    """
    blockbuster = BlockBuster(scanned_modules, excluded_modules=excluded_modules)
    blockbuster.activate()
    yield blockbuster
    blockbuster.deactivate()
