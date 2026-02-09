import ast
import asyncio
import base64
import io
import json
import logging
import shutil
import tarfile
import time
from pathlib import Path
from typing import Any

import docker  # type: ignore[import]

from oqtopus_engine_core.framework import (
    GlobalContext,
    Job,
    JobContext,
    JobRepository,
    Step,
)

logger = logging.getLogger(__name__)


class SseStep(Step):
    """Step to run SSE."""

    def __init__(
        self,
        runner_settings: dict[str, Any],
    ) -> None:

        self._settings = runner_settings
        logger.info(
            "SseStep was initialized",
            extra={"runner_settings": runner_settings},
        )

    async def pre_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,  # noqa: ARG002
        job: Job,
    ) -> None:
        """Pre-process the job by downloading the user program and run SSE.

        This method downloads the user's program from Oqtopus Cloud,
        saves it to a temporary directory, and Run SSE.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        Raises:
            RuntimeError: If the SSE run fails.

        """
        if job.job_type != "sse":
            logger.debug(
                "job_type is not sse, skipping",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            return

        # Update job status
        job.status = "running"
        await gctx.job_repository.update_job_status(job)

        config = self._settings
        # Make tmp dir
        temp_dirs = self._make_tmpdir(job.job_id, config["host_work_path"])

        try:
            # Download the python program from cloud and save it to
            # the temporary directory
            logger.debug(
                "downloading user program file",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            await self._prep_userprogram(
                job.job_id,
                gctx.job_repository,
                temp_dirs["in"],
                config["userprogram_name"]
            )

            # Run SSE - Start container, execute user program, get result and log
            await self._run_sse(job, gctx, config, temp_dirs)
        except RuntimeError:
            logger.exception(
                "SSE step failed",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            raise
        except Exception as e:
            logger.exception(
                "unexpected error occurred in SSE step",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            msg = "internal server error"
            raise RuntimeError(msg) from e
        finally:
            # Clean up temporary directory
            if config["delete_host_temp_dirs"]:
                self._delete_tmpdir(temp_dirs["base"])
        return

    async def post_process(
        self,
        gctx: GlobalContext,
        jctx: JobContext,
        job: Job,
    ) -> None:
        """Post-process the job.

        Do nothing.

        Args:
            gctx: The global context.
            jctx: The job context.
            job: The job object.

        """

    async def _run_sse(
        self,
        job: Job,
        gctx: GlobalContext,
        config: dict,
        temp_dirs: dict
    ) -> None:

        # Initialize SseRunner
        try:
            sse_runner = SseRunner(config, temp_dirs, job, gctx)
        except Exception as e:
            logger.exception(
                "failed to initialize SseRunner",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            msg = "internal server error"
            raise RuntimeError(msg) from e

        # Run docker container
        logger.info(
            "running SSE", extra={"job_id": job.job_id, "job_type": job.job_type}
        )
        try:
            start = time.perf_counter()
            await sse_runner.run_sse()
        except SseRuntimeError as e:
            # Exception raised when user program execution fails
            logger.exception(
                "user program execution error occurred during SSE",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            msg = "user program execution error"
            raise RuntimeError(msg) from e
        except Exception as e:
            # Other exceptions such as failure of container start, file copy, etc.
            logger.exception(
                "failed to run SSE",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            msg = "internal server error"
            raise RuntimeError(msg) from e
        else:
            job.status = sse_runner.result_job.status
            logger.info(
                "succeeded to run SSE",
                extra={"job_id": job.job_id, "job_type": job.job_type},
            )
            if job.status != "succeeded":
                # SSE completed but job status is not succeeded
                logger.error(
                    "but sse job status is not succeeded", extra={"job_id": job.job_id},
                )
                msg = job.job_info.message or "sse job failed"
                raise RuntimeError(msg)
        finally:
            elapsed_sec = time.perf_counter() - start
            self._set_result_to_job(job, sse_runner.result_job, elapsed_sec)
            await gctx.job_repository.update_job_transpiler_info(job)

    async def _prep_userprogram(
        self,
        job_id: str,
        job_repository: JobRepository,
        input_dir_path: Path,
        user_program_name: str
    ) -> str:
        userprogram_data = await self._download_userprogram(job_id, job_repository)
        await self._make_userprogram_file(
            userprogram_data,
            input_dir_path,
            user_program_name
        )

    @staticmethod
    async def _download_userprogram(job_id: str, job_repository: JobRepository) -> str:
        body = await job_repository.get_ssesrc(
            job_id=job_id,
        )
        body_bytes = ast.literal_eval(body)
        return body_bytes.decode("utf-8")

    @staticmethod
    async def _make_userprogram_file(
        userprogram_data: str, input_dir_path: Path, user_program_name: str
    ) -> None:
        # base64 decode
        try:
            decoded = base64.b64decode(userprogram_data)
        except Exception:
            logger.exception("failed to decode user program data from base64")
            raise

        # write to file
        file_path = input_dir_path / user_program_name
        try:
            file_path.write_bytes(decoded)
            file_path.chmod(0o600)
        except Exception:
            logger.exception("failed to write user program file")
            raise

    @staticmethod
    def _make_tmpdir(job_id: str, base_dir: str) -> dict[str, Path]:

        base = Path(base_dir) / job_id
        paths = {
            "base": base,
            "in": base / "in",
            "out": base / "out",
        }

        # make base tmp dir
        for target_dir in paths.values():
            try:
                target_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
            except Exception:
                logger.exception(
                    "failed to make temp directory",
                    extra={"job_id": job_id, "target_dir": target_dir},
                )
                raise
        logger.debug(
            "made temp directory", extra={"job_id": job_id, "target_dir": base}
        )
        return paths

    @staticmethod
    def _delete_tmpdir(path: Path) -> None:
        try:
            if path.exists():
                shutil.rmtree(path)
                logger.debug("deleted temp directory", extra={"target_dir": path})
        except Exception as e:
            msg = f"failed to delete temp directory {path}"
            raise RuntimeError(msg) from e

    @staticmethod
    def _set_result_to_job(
        job: Job, result_job: Job, execution_time_in_sec: float
    ) -> None:
        if result_job is None:
            return

        if result_job.job_info is not None:
            job.job_info = result_job.job_info

        job.transpiler_info = result_job.transpiler_info

        job.execution_time = execution_time_in_sec


class SseRunner:
    """Class to run user program inside Docker container for SSE.

    Args:
        config (dict): Configuration dictionary for SSE runner.
        temp_dirs (dict[str, Path]): Temporary directories for input and output.
        job (Job): Job object containing job details.
        job_api (JobApi): Job API client for interacting with the Oqtopus Cloud.

    """

    def __init__(
        self, config: dict, temp_dirs: dict[str, Path], job: Job, gctx: GlobalContext
    ) -> None:

        self._job = job
        self._job_id = job.job_id
        self._gctx = gctx
        self._container_name = job.job_id
        self._config = config
        self._docker_client = docker.DockerClient(base_url="unix://var/run/docker.sock")
        self._host_work_path = temp_dirs

        path = Path(self._config["container_work_path"])
        self._container_work_path = {
            "base": path,
            "in": path / ("in"),
            "out": path / ("out"),
        }

        self._container: docker.models.containers.Container = None
        self.result_job = None

        logger.info(
            "SseRunner was initialized",
            extra={
                "job_id": self._job_id,
                "config": self._config,
            },
        )

    @staticmethod
    def _is_file_size_valid(path: Path, max_file_size: int) -> bool:
        """Check if the file size is within the specified maximum limit.

        Args:
            path (Path): The path to the file.
            max_file_size (int): The maximum allowed file size in bytes.

        Returns:
            bool: True if the file size is within the limit, False otherwise.

        Raises:
            FileNotFoundError: If the file does not exist.
            IsADirectoryError: If the path is not a file.

        """
        if not path.exists():
            msg = f"The file does not exist: {path}"
            raise FileNotFoundError(msg)
        if not path.is_file():
            msg = f"The path is not a file: {path}"
            raise IsADirectoryError(msg)

        size = path.stat().st_size
        if size > max_file_size:
            logger.error(
                "the file size is larger than MaxFileSize",
                extra={"file_size": size, "max_file_size": max_file_size},
            )
            return False
        return True

    async def run_sse(self) -> None:
        """Run a user's program inside a Docker container.

        Args:
            None

        Raises:
            SseRuntimeError: If the user program execution fails.
            RuntimeError: If any other step in the SSE run fails.

        """
        logger.info("starting SSE run", extra={"job_id": self._job_id})

        # ======= preprocess container ========
        # Start container, initialize it and copy user's program into it
        try:
            await self._preprocess_container()
        except Exception:
            msg = f"failed to preprocess container. Job({self._job_id})"
            raise
        else:
            logger.debug(
                "container preprocessed successfully",
                extra={"job_id": self._job_id},
            )

        # ======= execute user's program ========
        try:
            logger.debug(
                "executing user program inside SSE container",
                extra={"job_id": self._job_id},
            )
            userprogram_container_path = (
                self._container_work_path["in"] / self._config["userprogram_name"]
            )
            cmd = f"uv run --project /app python {userprogram_container_path} 1> /proc/1/fd/1 2> /proc/1/fd/2"  # noqa: E501
            await self._exec_in_container(
                user="appuser",
                privileged=True,
                cmd=cmd,
                timeout_in_sec=self._config.get("timeout", 600),
            )
        except Exception:
            logger.exception(
                "failed to execute user program inside container",
                extra={"job_id": self._job_id},
            )
            exec_is_success = False
        else:
            logger.debug(
                "user program executed inside SSE container successfully",
                extra={"job_id": self._job_id},
            )
            exec_is_success = True

        # ======= postprocess container ========
        # Get result and log from container, upload the log to S3
        try:
            await self._postprocess_container(exec_is_success=exec_is_success)
        except Exception:
            logger.exception(
                "failed to get results and logs from container",
                extra={"job_id": self._job_id},
            )
            results_is_success = False
        else:
            results_is_success = True

        # ======= stop and remove container ========
        logger.debug(
            "stopping and removing SSE container",
            extra={"job_id": self._job_id},
        )
        self._stop_and_remove()

        # ======= check overall success ========
        if not exec_is_success:
            logger.error(
                "user program execution failed",
                extra={"job_id": self._job_id},
            )
            msg = f"user program execution failed. Job({self._job_id})"
            raise SseRuntimeError(msg)
        if not results_is_success:
            logger.error(
                "sse result retrieval failed",
                extra={"job_id": self._job_id},
            )
            msg = f"sse result retrieval failed. Job({self._job_id})"
            raise RuntimeError(msg)

        logger.info(
            "SSE completed successfully",
            extra={"job_id": self._job_id},
        )

    async def _preprocess_container(self) -> None:
        # ======== start container ========
        try:
            self._container = self._start_container()
        except Exception as e:
            msg = f"failed to start SSE container. Job({self._job_id})"
            raise RuntimeError(msg) from e

        if self._container is None:
            msg = f"failed to start SSE container. Job({self._job_id})"
            raise RuntimeError(msg)

        # ======== initialize container ========
        try:
            logger.debug(
                "initializing SSE container",
                extra={"job_id": self._job_id},
            )
            cmd = f'sh -c "/root/init.sh {self._config["sse_engine_port"]}"'
            await self._exec_in_container(
                user="root",
                privileged=True,
                cmd=cmd,
            )
        except Exception as e:
            self._stop_and_remove()
            msg = f"failed to initialize SSE container. Job({self._job_id})"
            raise RuntimeError(msg) from e
        else:
            logger.debug(
                "SSE container initialized successfully",
                extra={"job_id": self._job_id},
            )

        # ======== copy user program into container ========
        try:
            logger.debug(
                "copying user program into SSE container",
                extra={"job_id": self._job_id},
            )
            userprogram_host_path = (
                self._host_work_path["in"] / self._config["userprogram_name"]
            )
            with userprogram_host_path.open("rb") as f:
                content = f.read()
            await self._copy_user_program_into_container(
                user="appuser",
                user_program_name=self._config["userprogram_name"],
                user_program_content=content,
            )
        except Exception as e:
            self._stop_and_remove()
            msg = f"failed to copy user program into container. Job({self._job_id})"
            raise RuntimeError(msg) from e
        else:
            logger.debug(
                "user program copied into SSE container successfully",
                extra={"job_id": self._job_id},
            )

    async def _postprocess_container(
        self,
        exec_is_success: bool,  # noqa: FBT001
    ) -> None:
        # ======== get result file from container ========
        try:
            logger.debug(
                "getting result file from SSE container",
                extra={"job_id": self._job_id},
            )
            self.result_job = self._get_result_from_container(
                user="appuser",
                container_path=self._container_work_path["out"],
                filename=self._config["result_file_name"],
            )
        except Exception:
            logger.exception(
                "failed to get result file from container",
                extra={"job_id": self._job_id},
            )
            copy_is_success = False
            # error but continue to save log
        else:
            logger.debug(
                "result file got from SSE container successfully",
                extra={"job_id": self._job_id},
            )
            copy_is_success = True

        # ======== save container log ========
        try:
            logger.debug(
                "saving container log",
                extra={"job_id": self._job_id},
            )
            self._save_container_log(
                self._host_work_path["out"],
                self._config["log_file_name"],
                print_to_engine_log=(not exec_is_success),
            )
        except Exception:
            logger.exception(
                "failed to save container log",
                extra={"job_id": self._job_id},
            )
            log_is_success = False
        else:
            logger.debug(
                "container log saved successfully",
                extra={"job_id": self._job_id},
            )
            log_is_success = True

        # ======== upload container log to S3 ========
        upload_is_success = True
        if log_is_success:
            try:
                logger.debug(
                    "uploading log file to S3",
                    extra={"job_id": self._job_id},
                )
                await self._s3upload(
                    self._job_id,
                    self._host_work_path["out"],
                    self._config["log_file_name"],
                    int(self._config["max_file_size"]),
                )
            except Exception:
                logger.exception(
                    "failed to upload log file to S3",
                    extra={"job_id": self._job_id},
                )
                upload_is_success = False
            else:
                logger.debug(
                    "log file uploaded to S3 successfully",
                    extra={"job_id": self._job_id},
                )

        # ======== check overall success ========
        if not copy_is_success or not log_is_success or not upload_is_success:
            msg = "failed to get result, log or upload log from container"
            raise RuntimeError(msg)

    def _start_container(self) -> docker.models.containers.Container:
        logger.debug(
            "starting SSE runtime container",
            extra={
                "job_id": self._job_id,
                "container_name": self._container_name,
                "container_image": self._config["container_image"],
            },
        )

        # Create tmpfs volume
        tmpfs = docker.types.Mount(
            target="/sse",
            source="",
            type="tmpfs",
            tmpfs_size=self._config.get("container_disk_quota", 64 * 1024 * 1024),
        )
        # Set environment variables in container
        env_vars = [
            f"JOB_JSON={self._job.model_dump_json()}",
            f"IN_PATH={self._container_work_path['in']}",
            f"OUT_PATH={self._container_work_path['out']}",
            f"GRPC_SSE_ENGINE_PORT={self._config['sse_engine_port']}",
            "TERM=dumb",  # to drop ANSI escape codes in logs
        ]

        # Run container
        container = self._docker_client.containers.run(
            name=self._container_name,
            image=self._config["container_image"],
            command="/bin/sh",
            environment=env_vars,
            tty=True,
            detach=True,
            mounts=[tmpfs],
            mem_limit=self._config.get("container_memory", 256 * 1024 * 1024),
            cpuset_cpus=self._config.get("container_cpu_set", "0"),
            extra_hosts={"host.docker.internal": "host-gateway"},
        )
        logger.debug(
            "started SSE container successfully",
            extra={
                "job_id": self._job_id,
                "container_id": container.id,
            },
        )
        return container

    async def _exec_in_container(
        self,
        cmd: str,
        user: str,
        privileged: bool,  # noqa: FBT001
        timeout_in_sec: int = 10,
    ) -> None:
        options = {
            "cmd": ["sh", "-c", cmd],
            "detach": False,
            "user": user,
            "privileged": privileged,
            "stdin": False,
            "stdout": True,
            "stderr": True,
            "demux": False,
        }

        def exec_cmd(options: dict) -> int:
            exit_code = -1
            try:
                exit_code, _ = self._container.exec_run(**options)
            except docker.errors.APIError:
                logger.exception(
                    "docker API error in exec_cmd",
                    extra={"job_id": self._job_id},
                )
            except Exception:
                logger.exception(
                    "exception in exec_cmd",
                    extra={"job_id": self._job_id},
                )
            return exit_code

        try:
            # execute command in container with timeout
            async with asyncio.timeout(timeout_in_sec):
                exit_code = await asyncio.to_thread(exec_cmd, options)
            if exit_code != 0:
                msg = "command execution failed in container"
                raise RuntimeError(msg)
        except TimeoutError as e:
            msg = "timeout when executing command in container"
            raise TimeoutError(msg) from e

    async def _copy_user_program_into_container(
        self, user: str, user_program_name: str, user_program_content: bytes
    ) -> None:
        logger.debug(
            "copying user program into container",
            extra={"job_id": self._job_id},
        )

        # Create tar archive in memory
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as tar:
            info = tarfile.TarInfo(name=user_program_name)
            info.mode = 0o755
            info.size = len(user_program_content)
            tar.addfile(tarinfo=info, fileobj=io.BytesIO(user_program_content))
        data.seek(0)

        # Copy the tar archive into container tmpfs
        # Use /home/<user> as temporary path in container
        # because put_archive ends up with error when the target path is tmpfs
        tmp_dir = Path("/home") / user
        is_success = self._container.put_archive(path=tmp_dir, data=data.getvalue())
        if not is_success:
            msg = "failed to put archive into container tmpfs"
            raise RuntimeError(msg)

        # Move the transferred file from temporary path to tmpfs
        await self._exec_in_container(
            user=user,
            privileged=False,
            cmd=f"mv {tmp_dir / user_program_name} {self._container_work_path['in']}",
        )
        logger.debug(
            "user program copied to tmpfs successfully",
            extra={"job_id": self._job_id},
        )

    def _get_result_from_container(
        self, user: str, container_path: Path, filename: str
    ) -> dict[str, Any]:
        logger.debug(
            "getting file from container",
            extra={"job_id": self._job_id},
        )

        # Receive data from standard output and write it to a file on the host side
        cmd = f"tar -C {container_path} -c -f - {filename}"
        _, chunks = self._container.exec_run(
            ["sh", "-c", cmd],
            user=user,
            stdout=True,
            stderr=True,
            stream=True,
            demux=True,
        )

        # read tar data from chunks
        tar_data = io.BytesIO()
        error_msg = ""
        for stdout, stderr in chunks:
            if stderr:
                error_msg += stderr.decode("utf-8", errors="ignore")
            if stdout:
                tar_data.write(stdout)

        # if there is any error message
        if error_msg:
            msg = f"error when getting file from container: {error_msg}"
            raise RuntimeError(msg)

        # if no error, save the file
        tar_data.seek(0)
        # extract tar file and save to out_path
        with tarfile.open(fileobj=tar_data, mode="r") as tar:
            member = tar.getmember(filename)
            file_obj = tar.extractfile(member)
            if file_obj is None:
                msg = "result file not found in tar"
                raise RuntimeError(msg)

            result_str = file_obj.read().decode("utf-8", errors="ignore")
            if not result_str:
                msg = "result file is empty"
                raise RuntimeError(msg)

            logger.debug(
                "file got successfully",
                extra={"job_id": self._job_id},
            )
            return Job(**json.loads(result_str))

    def _save_container_log(
        self,
        out_path: Path,
        log_file_name: str,
        print_to_engine_log: bool,  # noqa: FBT001
    ) -> None:

        logs = self._container.logs(stdout=True, stderr=True, follow=False)
        content = logs.decode("utf-8", errors="ignore")
        if print_to_engine_log:
            logger.info(
                "container log",
                extra={"job_id": self._job_id, "container_log": content},
            )
        with (out_path / log_file_name).open("w") as f:
            f.write(content)

    def _stop_and_remove(self) -> None:
        logger.debug(
            "stopping container",
            extra={"job_id": self._job_id},
        )
        try:
            self._container.stop(timeout=0)
        except docker.errors.APIError:
            logger.exception(
                "docker API error when stopping container",
                extra={"job_id": self._job_id},
            )
        except Exception:
            logger.exception(
                "failed to stop container",
                extra={"job_id": self._job_id},
            )

        logger.debug(
            "removing container",
            extra={"job_id": self._job_id},
        )
        try:
            self._container.remove(v=True, force=True)
        except docker.errors.APIError:
            logger.exception(
                "docker API error when removing container",
                extra={"job_id": self._job_id},
            )
        except Exception:
            logger.exception(
                "failed to remove container",
                extra={"job_id": self._job_id},
            )
        logger.debug(
            "container stopped and removed",
            extra={"job_id": self._job_id},
        )

    async def _s3upload(
        self, job_id: str, file_path: Path, file_name: str, max_file_size: int
    ) -> None:
        logger.debug(
            "uploading file to S3", extra={"job_id": job_id, "file_name": file_name}
        )
        path = file_path / file_name

        try:
            is_valid_size = self._is_file_size_valid(path, max_file_size)
        except FileNotFoundError as e:
            msg = "file not found during S3 upload"
            raise FileNotFoundError(msg) from e
        except IsADirectoryError as e:
            msg = "invalid file path during S3 upload"
            raise IsADirectoryError(msg) from e

        if not is_valid_size:
            logger.error(
                "The size of the file is larger than the max file size",
                extra={"job_id": job_id, "max_file_size": max_file_size},
            )
            msg = "file size exceeds the maximum limit"
            raise ValueError(msg)

        try:
            await self._gctx.job_repository.update_sselog(
                job_id=job_id,
                sselog=str(path),
            )

            logger.debug(
                "file uploaded to S3 successfully",
                extra={"job_id": job_id, "file_name": file_name},
            )
        except Exception as e:
            msg = "failed to upload file to S3"
            raise RuntimeError(msg) from e


class SseRuntimeError(RuntimeError):
    """Exception raised for errors in the SSE runtime container."""
