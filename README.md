# Blockbuster

Blockbuster is a Python package designed to detect and prevent blocking calls within an asynchronous event loop.
It is particularly useful when executing tests to ensure that your asynchronous code does not inadvertently call blocking operations, 
which can lead to performance bottlenecks and unpredictable behavior.

In Python, the asynchronous event loop allows for concurrent execution of tasks without the need for multiple threads or processes.
This is achieved by running tasks cooperatively, where tasks yield control back to the event loop when they are waiting for I/O operations or other long-running tasks to complete.

However, blocking calls, such as file I/O operations or certain networking operations, can halt the entire event loop, preventing other tasks from running.
This can lead to increased latency and reduced performance, defeating the purpose of using asynchronous programming.

The difficulty with blocking calls is that they are not always obvious, especially when working with third-party libraries or legacy code.
This is where Blockbuster comes in: it helps you identify and eliminate blocking calls in your codebase during testing, ensuring that your asynchronous code runs smoothly and efficiently.
It does this by wrapping common blocking functions and raising an exception when they are called within an asynchronous context.

Notes:
- Blockbuster currently only detects `asyncio` event loops.
- Blockbuster is tested only with CPython. It may work with other Python implementations if it's possible to monkey-patch the functions with `setattr`.

## Installation

The package is named `blockbuster`.
For instance with `pip`:

```bash
pip install blockbuster
```

It is recommended to constrain the version of Blockbuster.
Blockbuster doesn't strictly follow semver. Breaking changes such as new rules added may be introduced between minor versions, but not between patch versions.
So it is recommended to constrain the Blockbuster version on the minor version.
For instance, with `uv`:

```bash
uv add "blockbuster>=1.5.5,<1.6"
```

## Using BlockBuster

### Manually

To activate BlockBuster manually, create an instance of the `BlockBuster` class and call the `activate()` method:

```python
from blockbuster import BlockBuster

blockbuster = BlockBuster()
blockbuster.activate()
```

Once activated, BlockBuster will raise a `BlockingError` exception whenever a blocking call is detected within an `asyncio` event loop.

To deactivate BlockBuster, call the `deactivate()` method:

```python
blockbuster.deactivate()
```

### Using the context manager

BlockBuster can also be activated using a context manager, which automatically activates and deactivates the checks within the `with` block:

```python
from blockbuster import blockbuster_ctx

with blockbuster_ctx():
    # Your test code here
```

### Usage with Pytest

Blockbuster is intended to be used with testing frameworks like `pytest` to catch blocking calls. 
Here's how you can integrate Blockbuster into your `pytest` test suite:

```python
import pytest
import time
from blockbuster import BlockBuster, BlockingError, blockbuster_ctx
from typing import Iterator

@pytest.fixture(autouse=True)
def blockbuster() -> Iterator[BlockBuster]:
    with blockbuster_ctx() as bb:
        yield bb

async def test_time_sleep() -> None:
    with pytest.raises(BlockingError, match="sleep"):
        time.sleep(1)  # This should raise a BlockingError
```

By using the `blockbuster_ctx` context manager, Blockbuster is automatically activated for every test, and blocking calls will raise a `BlockingError`.

## How it works

Blockbuster works by wrapping common blocking functions from various modules (e.g., `os`, `socket`, `time`) and replacing them with versions that check if they are being called from within an `asyncio` event loop.
If such a call is detected, Blockbuster raises a `BlockingError` to indicate that a blocking operation is being performed inappropriately.

Blockbuster supports by default the following functions and modules:

- **Time Functions**:
  - `time.sleep`
- **OS Functions**:
  - `os.getcwd`
  - `os.statvfs`
  - `os.sendfile`
  - `os.rename`
  - `os.remove`
  - `os.unlink`
  - `os.mkdir`
  - `os.rmdir`
  - `os.link`
  - `os.symlink`
  - `os.readlink`
  - `os.listdir`
  - `os.scandir`
  - `os.access`
  - `os.stat`
  - `os.replace`
  - `os.read`
  - `os.write`
- **OS path Functions**:
  - `os.path.ismount`
  - `os.path.samestat`
  - `os.path.sameopenfile`
  - `os.path.islink`
  - `os.path.abspath`
- **IO Functions**:
  - `io.BufferedReader.read`
  - `io.BufferedWriter.write`
  - `io.BufferedRandom.read`
  - `io.BufferedRandom.write`
  - `io.TextIOWrapper.read`
  - `io.TextIOWrapper.write`
- **Socket Functions**:
  - `socket.socket.connect`
  - `socket.socket.accept`
  - `socket.socket.send`
  - `socket.socket.sendall`
  - `socket.socket.sendto`
  - `socket.socket.recv`
  - `socket.socket.recv_into`
  - `socket.socket.recvfrom`
  - `socket.socket.recvfrom_into`
  - `socket.socket.recvmsg`
  - `ssl.SSLSocket.write`
  - `ssl.SSLSocket.send`
  - `ssl.SSLSocket.read`
  - `ssl.SSLSocket.recv`
- **SQLite Functions**:
  - `sqlite3.Cursor.execute`
  - `sqlite3.Cursor.executemany`
  - `sqlite3.Cursor.executescript`
  - `sqlite3.Cursor.fetchone`
  - `sqlite3.Cursor.fetchmany`
  - `sqlite3.Cursor.fetchall`
  - `sqlite3.Connection.execute`
  - `sqlite3.Connection.executemany`
  - `sqlite3.Connection.executescript`
  - `sqlite3.Connection.commit`
  - `sqlite3.Connection.rollback`
- **Thread lock Functions**:
  - `threading.Lock.acquire`
  - `threading.Lock.acquire_lock`
- **Built-in Functions**:
  - `input`

Some exceptions to the rules are already in place:
- Importing modules does blocking calls as it interacts with the file system. Since this operation is cached and very hard to avoid, it is excluded from the detection.
- Blocking calls done by the `pydevd` debugger.
- Blocking calls done by the `pytest` framework.

## Customizing Blockbuster

### Adding custom rules

Blockbuster is not a silver bullet and may not catch all blocking calls.
In particular, it will not catch blocking calls that are done by third-party libraries that do blocking calls in C extensions.
For these third-party libraries, you can declare your own custom rules to Blockbuster to catch these blocking calls.

Eg.:
```python
from blockbuster import BlockBuster, BlockBusterFunction
import mymodule

blockbuster = BlockBuster()
blockbuster.functions["my_module.my_function"] = BlockBusterFunction(my_module, "my_function")
blockbuster.activate()
```

Note: if blockbuster has already been activated, you will need to activate the custom rule yourself.

```python
from blockbuster import blockbuster_ctx, BlockBusterFunction
import mymodule

with blockbuster_ctx() as blockbuster:
    blockbuster.functions["my_module.my_function"] = BlockBusterFunction(my_module, "my_function")
    blockbuster.functions["my_module.my_function"].activate()
```

### Allowing blocking calls in specific contexts

You can customize Blockbuster to allow blocking calls in specific functions by using the `can_block_in` method of the `BlockBusterFunction` class.
This method allows you to specify exceptions for particular files and functions where blocking calls are allowed.

```python
from blockbuster import BlockBuster

blockbuster = BlockBuster()
blockbuster.activate()
blockbuster.functions["os.stat"].can_block_in("specific_file.py", {"allowed_function"})
```

### Deactivating specific checks

If you need to deactivate specific checks, you can directly call the `deactivate` method on the corresponding `BlockBusterFunction` instance:

```python
from blockbuster import BlockBuster

blockbuster = BlockBuster()
blockbuster.activate()
blockbuster.functions["socket.socket.connect"].deactivate()
```

## Contributing

Contributions are welcome! If you encounter any issues or have suggestions for improvements, please open an issue on the GitHub repository.

### Development Setup

Blockbuster uses `uv` to manage its development environment.
See the [uv documentation](https://docs.astral.sh/uv/getting-started/installation/) for more informationand how to install it.

To install the required dependencies, run the following command:

```bash
uv sync
```

### Running Tests

Tests are written using `pytest`.
To run the tests, use the following command:

```bash
uv run pytest
```

### Code Formatting

Code formatting is done using `ruff`. To format the code, run the following command:

```bash
uv run ruff format
```

### Code Linting

Code linting is done using `ruff`.
To lint the code, fixing any issues that can be automatically fixed, run the following command:

```bash
uv run ruff check --fix
```

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

Blockbuster uses the [forbiddenfruit](https://clarete.li/forbiddenfruit/) library to monkey-patch CPython immutable builtin functions and methods.

Blockbuster was greatly inspired by the [BlockHound](https://github.com/reactor/BlockHound) library for Java, which serves a similar purpose of detecting blocking calls in JVM reactive applications.
