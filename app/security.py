"""Bound request bodies before parsing and attach defense-in-depth headers."""
import asyncio
from starlette.responses import JSONResponse


class SecurityEnvelope:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)

        async def secure_send(message):
            if message['type'] == 'http.response.start':
                headers = dict(message.get('headers', []))
                headers.update({
                    b'x-content-type-options': b'nosniff',
                    b'x-frame-options': b'DENY',
                    b'referrer-policy': b'no-referrer',
                    b'permissions-policy': b'camera=(), microphone=(), geolocation=()',
                    b'content-security-policy': b"frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
                    b'cache-control': b'no-store',
                })
                # Keep repeated Set-Cookie headers intact.
                message['headers'] = [(k, v) for k, v in message.get('headers', []) if k == b'set-cookie'] + [(k, v) for k, v in headers.items() if k != b'set-cookie']
            await send(message)

        limit = 8192 if scope['path'].startswith('/auth/') else 4 * 1024 * 1024
        headers = dict(scope.get('headers', []))
        try:
            length = int(headers.get(b'content-length', b'0'))
            if length < 0:
                raise ValueError()
        except ValueError:
            return await JSONResponse({'detail':'Invalid content length'}, status_code=400)(scope, receive, secure_send)
        if length > limit:
            return await JSONResponse({'detail':'Request body too large'}, status_code=413)(scope, receive, secure_send)
        chunks, size = [], 0
        try:
            async with asyncio.timeout(10):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    body = message.get('body', b'')
                    size += len(body)
                    if size > limit:
                        return await JSONResponse({'detail':'Request body too large'}, status_code=413)(scope, receive, secure_send)
                    chunks.append(body)
                    if not message.get('more_body', False):
                        break
        except TimeoutError:
            return await JSONResponse({'detail':'Request body timed out'}, status_code=408)(scope, receive, secure_send)
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type':'http.request', 'body':b''.join(chunks), 'more_body':False}
            return await receive()

        await self.app(scope, bounded_receive, secure_send)
