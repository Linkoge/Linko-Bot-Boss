"""Minimal stdio MCP server exposing a thread's application tools to Claude Code.

Started by Claude Code from the host's --mcp-config as ``python -I claude_mcp.py SOCKET THREAD``.
Every call is forwarded over the host's private Unix socket; no package imports or state here.
"""

import json
import socket
import sys


def host(sock_path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.connect(sock_path)
        conn.sendall((json.dumps(payload) + '\n').encode())
        data = b''
        while not data.endswith(b'\n'):
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data)


def main():
    sock_path, thread = sys.argv[1], sys.argv[2]
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if 'id' not in message:
            continue
        method, params = message.get('method'), message.get('params') or {}
        try:
            if method == 'initialize':
                result = {'protocolVersion': params.get('protocolVersion', '2025-06-18'),
                          'capabilities': {'tools': {}}, 'serverInfo': {'name': 'linko', 'version': '1'}}
            elif method == 'tools/list':
                result = {'tools': host(sock_path, {'op': 'list', 'thread': thread})['tools']}
            elif method == 'tools/call':
                reply = host(sock_path, {'thread': thread, 'tool': params['name'], 'arguments': params.get('arguments')})
                result = {'content': [{'type': 'text', 'text': reply['text']}], 'isError': not reply['success']}
            elif method == 'ping':
                result = {}
            else:
                raise LookupError(method)
            response = {'jsonrpc': '2.0', 'id': message['id'], 'result': result}
        except LookupError as error:
            response = {'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': f'Unsupported {error}'}}
        except Exception as error:
            response = {'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32000, 'message': str(error)[:2000]}}
        sys.stdout.write(json.dumps(response) + '\n')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
