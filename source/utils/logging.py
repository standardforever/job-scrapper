from starlette.middleware.base import BaseHTTPMiddleware


import logging
from logging.handlers import RotatingFileHandler
import json
from datetime import datetime, timezone
from enum import Enum

class APP_INIT(Enum):
	DATABASE = "DATABASE_INIT"
	DATA_CACHE = "DATA_CACHE_INIT"
	RPC_CLIENT = "RPC_CLIENT_INIT"
	RPC_SERVER = "RPC_SERVER_INIT"

class RUNTIME(Enum):
	HEARTBEAT = "HEARTBEAT"
	USER_LOGIN = "USER_LOGIN"
	USER_LOGIN_API_KEY = "USER_LOGIN_API_KEY"
	USER_CREATE_API_KEY = "USER_CREATE_API_KEY"
	USER_DELETE_API_KEY = "USER_DELETE_API_KEY"
	TEXT_ENGINE_RUN = "TEXT_ENGINE_RUN"
	TEXT_ENGINE_GET_RESULT = "TEXT_ENGINE_GET_RESULT"
	TEXT_ENGINE_GET_RUN_HISTORY = "TEXT_ENGINE_GET_RUN_HISTORY"
	RPC_CLIENT_REQUEST = "RPC_CLIENT_REQUEST"
	RPC_SERVER_RESPONSE = "RPC_SERVER_RESPONSE"
	USER_CREATE_ACCOUNT = "USER_CREATE_ACCOUNT"
	USER_ACTIVATE_ACCOUNT = "USER_ACTIVATE_ACCOUNT"
	USER_RESET_PASSWORD = "USER_RESET_PASSWORD"
	USER_FORGET_PASSWORD = "USER_FORGET_PASSWORD"
	USER_RESEND_ACTIVATION_EMAIL = "USER_RESEND_ACTIVATION_EMAIL"
	WORK_FLOW_GET = "WORK_FLOW_GET"
	WORK_FLOW_GET_BY_ORGANIZATION = "WORK_FLOW_GET_BY_ORGANIZATION"
	WORK_FLOW_CREATE = "WORK_FLOW_CREATE"
	WORK_FLOW_UPDATE = "WORK_FLOW_UPDATE"
	WORK_FLOW_DELETE = "WORK_FLOW_DELETE"
	WORK_FLOW_RUN = "WORK_FLOW_RUN"

class RPC_CLIENT(Enum):
	VERIFY_USER_TOKEN = "VERIFY_USER_TOKEN"

# Custom JSON Formatter
class JsonFormatter(logging.Formatter):
	def formatTime(self, record, datefmt=None):
		# Return time in ISO8601 format using timezone-aware datetime
		dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
		return dt.isoformat()

	def format(self, record):
		exclude_attrs = {"args", "asctime", "created", "exc_info", "exc_text", "filename",
						 "id", "levelno", "lineno", "message", "module", "msecs", "funcName", "msg", "pathname",
						 "process", "processName", "relativeCreated", "stack_info", "thread",
						 "threadName", "levelname"}

		log_record = {
			"timestamp": self.formatTime(record, self.datefmt),
			"function": record.funcName,
			"message": record.getMessage(),
			"module": record.module,
			"level": record.levelname,
		}

		# Include all other attributes dynamically
		for attr, value in record.__dict__.items():
			if attr not in exclude_attrs:
				log_record[attr] = value

		return json.dumps(log_record)

def setup_logger(name):
	logger = logging.getLogger(name)
	logger.setLevel(logging.INFO)

	# Configure streamhandler to stdout
	stream_handler = logging.StreamHandler()
	stream_handler.setFormatter(JsonFormatter())
	logger.addHandler(stream_handler)

	# Configure rotating handler to file
	rotating_handler = RotatingFileHandler("app.log", maxBytes=2*1024*1024, backupCount=10)
	rotating_handler.setFormatter(JsonFormatter())
	logger.addHandler(rotating_handler)

	return logger
