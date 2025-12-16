from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
import uuid


class TraceIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        """ Add the trace_id to request header"""
        trace_id = request.headers.get("X-Trace-ID", None)

        if trace_id is None:
            trace_id = str(uuid.uuid4())

        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers['X-Trace-ID'] = trace_id

        return response


def get_trace_id(request: Request) -> str | None:
    """ Get the trace_id from request"""
    try:
        return request.state.trace_id
    except:
        return None