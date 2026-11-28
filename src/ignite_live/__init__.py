"""Ignite application modules."""

import concurrent.futures as _futures

# Python 3.15 imports concurrent.futures' executors lazily (PEP 810). Importing PySide6 before one of them is
# first used breaks that ("'lazy_import' object is not callable" from every asyncio.to_thread), so load the one
# Ignite uses now: every entry point and test imports this package before PySide6. (Not the process pool: Ignite
# never uses it, and it would load multiprocessing at every start.)
_futures.ThreadPoolExecutor  # noqa: B018

__version__ = "1.9.3"
