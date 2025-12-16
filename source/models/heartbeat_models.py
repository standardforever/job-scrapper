from pydantic import BaseModel, Field
from datetime import datetime


class VersionInfo(BaseModel):
    major: int
    minor: int
    patch: int
    suffix: str

class HeartbeatModel(BaseModel):
    status: str = "OK"
    timestamp: datetime  = Field(default_factory=datetime.now)
    app_version: VersionInfo
    common_version: VersionInfo

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {
            # ObjectId: str,
            datetime: lambda dt: dt.isoformat()
        }