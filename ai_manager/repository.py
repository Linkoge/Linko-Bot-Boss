"""Manager repository inspection: filesystem reads only, shared by both providers."""

import os
import time
from itertools import islice
from pathlib import Path

SEARCH_SKIP = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'vendor', 'build', 'dist'}
PAGE = 50


def inspect_repository(root, action, path='.', query='', offset=0):
    root = root.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError('Path must stay inside the selected project repository')
    if not isinstance(offset, int) or offset < 0:
        raise ValueError('Offset must be a nonnegative integer')
    if action == 'read_document':
        if not target.is_file():
            raise ValueError('Read a project text file')
        with target.open(errors='replace') as source:
            content = source.read(offset + 12001)
        return {'path': path, 'content': content[offset:offset + 12000],
                'next_offset': offset + 12000 if len(content) > offset + 12000 else None}
    if action == 'list_files':
        if not target.is_dir():
            raise ValueError('List a project directory')
        entries = sorted(p.name + ('/' if p.is_dir() else '') for p in target.iterdir() if p.name != '.git')
        return {'path': path, 'entries': entries[offset:offset + PAGE],
                'next_offset': offset + PAGE if len(entries) > offset + PAGE else None}
    if action != 'search_code':
        raise ValueError('Unsupported repository read action')
    if not query:
        raise ValueError('A nonempty literal search query is required')
    if not target.exists():
        raise ValueError('Search a project file or directory')
    deadline = time.monotonic() + 5

    def check_time():
        if time.monotonic() > deadline:
            raise ValueError('Search exceeded 5 seconds; narrow path to a file or subdirectory')

    def files():
        if target.is_file():
            yield target
            return
        for directory, dirs, names in os.walk(target, followlinks=False):
            check_time()
            dirs[:] = sorted(d for d in dirs if d not in SEARCH_SKIP)
            for name in sorted(names):
                check_time()
                candidate = Path(directory) / name
                if candidate.resolve().is_relative_to(root) and candidate.is_file():
                    yield candidate

    def matches():
        for candidate in files():
            check_time()
            try:
                with candidate.open(errors='replace') as source:
                    if '\0' in source.read(1024):
                        continue
                    source.seek(0)
                    for number, line in enumerate(source, 1):
                        check_time()
                        if query in line:
                            yield {'path': candidate.relative_to(root).as_posix(), 'line': number,
                                   'text': line.rstrip('\r\n')[:300]}
            except OSError:
                continue  # A concurrently replaced or unreadable file must not abort the whole search.

    page = list(islice(matches(), offset, offset + PAGE + 1))
    return {'path': path, 'query': query, 'matches': page[:PAGE],
            'next_offset': offset + PAGE if len(page) > PAGE else None}
