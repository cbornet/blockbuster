"""BlockBuster module."""

import asyncio
import inspect
import io
import os
import socket
import ssl
import sys
import time
from contextlib import contextmanager

import forbiddenfruit


class BlockingError(Exception):
    """BlockingError class."""


def _blocking_error(func):
    if inspect.isbuiltin(func):
        msg = f"Blocking call to {func.__qualname__} ({func.__self__})"
    elif inspect.ismethoddescriptor(func):
        msg = f"Blocking call to {func}"
    else:
        msg = f"Blocking call to {func.__module__}.{func.__qualname__}"
    return BlockingError(msg)


def wrap_blocking(func, stack_excludes, func_excludes):
    """Wrap blocking function."""

    def wrapper(*args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return func(*args, **kwargs)
        for filename, functions in stack_excludes:
            for frame_info in inspect.stack():
                if (
                    frame_info.filename.endswith(filename)
                    and frame_info.function in functions
                ):
                    return func(*args, **kwargs)
        for func_exclude in func_excludes:
            if func_exclude(*args, **kwargs):
                return func(*args, **kwargs)
        raise _blocking_error(func)

    return wrapper


class BlockBusterFunction:
    """BlockBusterFunction class."""

    def __init__(
        self,
        module,
        func_name: str,
        *,
        is_immutable=False,
        stack_excludes=None,
        func_excludes=None,
        checker_func=wrap_blocking,
    ):
        """Initialize BlockBusterFunction."""
        self.module = module
        self.func_name = func_name
        self.original_func = getattr(module, func_name)
        self.is_immutable = is_immutable
        self.stack_excludes = stack_excludes or []
        self.func_excludes = func_excludes or []
        self.checker_func = checker_func

    def wrap_blocking(self):
        """Wrap the function."""
        checker = self.checker_func(
            self.original_func, self.stack_excludes, self.func_excludes
        )
        if self.is_immutable:
            forbiddenfruit.curse(self.module, self.func_name, checker)
        else:
            setattr(self.module, self.func_name, checker)

    def unwrap_blocking(self):
        """Unwrap the function."""
        if self.is_immutable:
            forbiddenfruit.curse(self.module, self.func_name, self.original_func)
        else:
            setattr(self.module, self.func_name, self.original_func)


def _get_time_wrapped_functions():
    return {
        "time.sleep": BlockBusterFunction(
            time,
            "sleep",
            stack_excludes=[("pydev/pydevd.py", {"_do_wait_suspend"})],
        )
    }


def _get_os_wrapped_functions():
    def os_exclude(fd, *_, **__):
        return not os.get_blocking(fd)

    return {
        "os.read": BlockBusterFunction(os, "read", func_excludes=[os_exclude]),
        "os.write": BlockBusterFunction(os, "write", func_excludes=[os_exclude]),
    }


def _get_io_wrapped_functions():
    def file_write_exclude(file, *_, **__):
        return file in {sys.stdout, sys.stderr}

    return {
        "io.BufferedReader.read": BlockBusterFunction(
            io.BufferedReader,
            "read",
            is_immutable=True,
            stack_excludes=[
                ("<frozen importlib._bootstrap_external>", {"get_data"}),
                ("_pytest/assertion/rewrite.py", {"_rewrite_test", "_read_pyc"}),
            ],
        ),
        "io.BufferedWriter.write": BlockBusterFunction(
            io.BufferedWriter,
            "write",
            is_immutable=True,
            stack_excludes=[("_pytest/assertion/rewrite.py", {"_write_pyc"})],
            func_excludes=[file_write_exclude],
        ),
        "io.BufferedRandom.read": BlockBusterFunction(
            io.BufferedRandom, "read", is_immutable=True
        ),
        "io.BufferedRandom.write": BlockBusterFunction(
            io.BufferedRandom,
            "write",
            is_immutable=True,
            func_excludes=[file_write_exclude],
        ),
        "io.TextIOWrapper.read": BlockBusterFunction(
            io.TextIOWrapper, "read", is_immutable=True
        ),
        "io.TextIOWrapper.write": BlockBusterFunction(
            io.TextIOWrapper,
            "write",
            is_immutable=True,
            func_excludes=[file_write_exclude],
        ),
    }


def _socket_exclude(sock, *_, **__):
    return not sock.getblocking()


def _get_socket_wrapped_functions():
    return {
        f"socket.socket.{method}": BlockBusterFunction(
            socket.socket, method, func_excludes=[_socket_exclude]
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


def _get_ssl_wrapped_functions():
    return {
        f"ssl.SSLSocket.{method}": BlockBusterFunction(
            ssl.SSLSocket, method, func_excludes=[_socket_exclude]
        )
        for method in ("write", "send", "read", "recv")
    }


class BlockBuster:
    """BlockBuster class."""

    def __init__(self):
        """Initialize BlockBuster."""
        self.wrapped_functions = (
            _get_time_wrapped_functions()
            | _get_os_wrapped_functions()
            | _get_io_wrapped_functions()
            | _get_socket_wrapped_functions()
            | _get_ssl_wrapped_functions()
        )

    def init(self):
        """Wrap all functions."""
        for wrapped_function in self.wrapped_functions.values():
            wrapped_function.wrap_blocking()

    def cleanup(self):
        """Unwrap all wrapped functions."""
        for wrapped_function in self.wrapped_functions.values():
            wrapped_function.unwrap_blocking()


@contextmanager
def blockbuster_ctx():
    """Context manager for using BlockBuster."""
    blockbuster = BlockBuster()
    blockbuster.init()
    yield blockbuster
    blockbuster.cleanup()
