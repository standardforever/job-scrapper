import os
import json
from models.heartbeat_models import VersionInfo

def load_app_version_info():
    ### File path of version.json for App is at the current working directory.
    app_dir = os.getcwd()
    file_path = os.path.join(app_dir, 'version.json')

    with open(file_path) as json_file:
        data = json.load(json_file)

    return VersionInfo(**data)

def load_common_version_info():
    ## File path of version.json for Common code is at ../version.json
    common_code_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.abspath(os.path.join(common_code_dir, os.pardir))
    file_path = os.path.join(parent_dir, 'version.json')

    with open(file_path) as json_file:
        data = json.load(json_file)

    return VersionInfo(**data)

APP_VERSION_INFO = load_app_version_info()
COMMON_VERSION_INFO = load_common_version_info()
