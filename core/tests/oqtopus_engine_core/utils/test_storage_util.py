from pathlib import Path
from zipfile import ZipFile

from oqtopus_engine_core.interfaces.oqtopus_cloud.models import (
    DevicesDeviceInfoUploadPresignedURL,
)
from oqtopus_engine_core.utils.storage_util import OqtopusStorage


def test_upload_writes_device_info_zip_file_url(tmp_path: Path):
    output_path = tmp_path / "devices" / "qulacs" / "device_info.zip"
    presigned_url = DevicesDeviceInfoUploadPresignedURL(
        url=output_path.as_uri(),
        fields={"key": "devices/qulacs/device_info.zip"},
    )

    OqtopusStorage.upload(
        presigned_url=presigned_url,
        data='{"device_id":"qulacs"}',
        arcname="device_info.json",
    )

    with ZipFile(output_path) as archive:
        assert archive.namelist() == ["device_info.json"]
        assert archive.read("device_info.json") == b'{"device_id":"qulacs"}'
