from utils import APP_VERSION_INFO, COMMON_VERSION_INFO
from models.heartbeat_models import VersionInfo, HeartbeatModel

from utils.logging import setup_logger, RUNTIME
logger = setup_logger(__name__)

async def get_heartbeat() -> HeartbeatModel:
	logger.debug("", extra={
		"operation": str(RUNTIME.HEARTBEAT),
		"app_version": "{}.{}.{}".format(APP_VERSION_INFO.major, APP_VERSION_INFO.minor, APP_VERSION_INFO.patch),
		"common_version": "{}.{}.{}".format(COMMON_VERSION_INFO.major, COMMON_VERSION_INFO.minor, COMMON_VERSION_INFO.patch)
		})
	return HeartbeatModel(
		app_version=APP_VERSION_INFO,
		common_version=COMMON_VERSION_INFO
		)