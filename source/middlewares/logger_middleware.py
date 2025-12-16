from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import  Request
from utils.logging import  setup_logger

logger = setup_logger(__name__)

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Log request details
        client_ip = request.client.host
        method = request.method
        url = request.url.path
        logger.info("", extra={
            "operation": "Middleware Request logging",
            "client_ip": client_ip
		})
        
        # Process the request
        response = await call_next(request)

        # Log response details
        status_code = response.status_code

        logger.info("", extra={
            "operation": "Middleware Response logging",
            "client_ip": client_ip,
            "status_code": status_code
		})

        return response