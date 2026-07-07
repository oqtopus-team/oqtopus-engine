import json
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

import requests
from requests.exceptions import RequestException

from oqtopus_engine_core.interfaces.oqtopus_cloud import (
    DevicesDeviceInfoUploadPresignedURL,
    JobsJobInfoUploadPresignedURL,
)


class OqtopusStorageError(Exception):
    """Custom exception for OqtopusStorage operations."""


class OqtopusStorage:
    """Provides methods for accessing oqtopus cloud storage via presign URLs."""

    DEFAULT_TIMEOUT_S = 60

    @staticmethod
    def _extract_zip_object(zip_bytes: bytes) -> dict[str, Any]:
        try:
            with ZipFile(BytesIO(zip_bytes), "r") as zip_arch:
                json_file_path_list = zip_arch.namelist()

                if len(json_file_path_list) != 1:
                    msg = (
                        "Expected one file in single ZIP archive, "
                        f"but found {len(json_file_path_list)}."
                    )
                    raise OqtopusStorageError(msg)

                with zip_arch.open(json_file_path_list[0]) as json_file:
                    data = json.loads(json_file.read())
                    if not isinstance(data, dict):
                        msg = (
                            "Expected JSON root to be an object (dict)"
                            f" but got {type(data).__name__}."
                        )
                        raise OqtopusStorageError(msg)
                    return data

        except BadZipFile as e:
            msg = "Invalid ZIP file"
            raise OqtopusStorageError(msg) from e
        except json.JSONDecodeError as e:
            msg = "Invalid JSON in ZIP file"
            raise OqtopusStorageError(msg) from e

    @staticmethod
    def download(
        presigned_url: str,
        proxies: dict[str, str] | None = None,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Download and extract JSON data from an oqtopus cloud storage .zip file.

        Args:
            presigned_url (str):
                presigned URL of target .zip file to download
            proxies: connection proxies to use
            timeout_s: operation timeout in seconds

        Returns:
            dict[str, Any]: loaded json data extracted from the .zip

        Raises:
            OqtopusStorageError: If the download, extraction, or JSON parsing fails.

        """
        try:
            resp = requests.get(url=presigned_url, proxies=proxies, timeout=timeout_s)
            resp.raise_for_status()
            return OqtopusStorage._extract_zip_object(resp.content)
        except RequestException as e:
            msg = f"Network error during download: {e}"
            raise OqtopusStorageError(msg) from e

    @staticmethod
    def upload(  # noqa: PLR0913, PLR0917
        presigned_url: (
            JobsJobInfoUploadPresignedURL | DevicesDeviceInfoUploadPresignedURL
        ),
        data: dict[str, Any] | str,
        arcname_ext: str = "",
        arcname: str | None = None,
        max_size: int | None = None,
        proxies: dict[str, str] | None = None,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> None:
        """Upload data to oqtopus cloud storage as .zip file.

        Args:
            presigned_url (JobsJobInfoUploadPresignedURL): presigned URL for upload
            data (dict[str, Any]): data to upload
            arcname_ext: data file extension to be zipped e.g. `.json`
            arcname: override filename to store inside the zip archive
            max_size: maximum size of the .zip file in bytes
            proxies: connection proxies to use
            timeout_s: operation timeout in seconds

        Raises:
            OqtopusStorageError: If the upload fails.

        """
        try:
            fields = presigned_url.fields
            if isinstance(fields, dict):
                original_fields = fields
                storage_key = str(fields["key"])
            else:
                # swagger-codegen generates JobsJobInfoUploadPresignedURLFields class
                # and changes fields names e.g. AWSAccessKeyId -> aws_access_key_id
                # we get the true field names
                original_fields = {
                    fields.attribute_map[k]: v
                    for (k, v) in fields.to_dict().items()
                }
                storage_key = str(fields.key)

            with BytesIO() as zip_buffer:
                zip_buffer.name = Path(storage_key).name
                with ZipFile(
                    file=zip_buffer, mode="w", compression=ZIP_DEFLATED
                ) as zip_arch:
                    archive_name = (
                        arcname
                        if arcname is not None
                        else f"{Path(zip_buffer.name).stem}{arcname_ext}"
                    )
                    zip_arch.writestr(
                        zinfo_or_arcname=archive_name,
                        data=data if isinstance(data, str) else json.dumps(data),
                    )
                zip_buffer.seek(0)

                if max_size is not None:
                    zipped_file_size = len(zip_buffer.getvalue())
                    if zipped_file_size > max_size:
                        msg = (
                            f"{zip_buffer.name} size: {zipped_file_size}B "
                            "is larger than the max file size "
                            f"{max_size}B"
                        )
                        raise OqtopusStorageError(msg)

                parsed_url = urlparse(presigned_url.url)
                if parsed_url.scheme == "file":
                    output_path = Path(parsed_url.path)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(zip_buffer.getvalue())
                    return

                resp = requests.post(
                    url=presigned_url.url,
                    data=original_fields,
                    files={
                        "file": (
                            Path(zip_buffer.name).name,
                            zip_buffer,
                            "application/zip",
                        )
                    },
                    proxies=proxies,
                    timeout=timeout_s,
                )
                resp.raise_for_status()

        except RequestException as e:
            msg = f"Network error during upload: {e}"
            raise OqtopusStorageError(msg) from e
