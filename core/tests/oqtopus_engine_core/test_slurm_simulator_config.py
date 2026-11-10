from pathlib import Path

from oqtopus_engine_core.framework import PipelineBuilder
from oqtopus_util.config import load_config
from oqtopus_util.di import DiContainer


def test_slurm_config_wires_shared_runtime_components(monkeypatch):
    monkeypatch.setenv("SLURM_PARTITION", "test-partition")
    monkeypatch.setenv("SLURM_DEVICE_N_QUBITS", "40")
    monkeypatch.setenv("SLURM_QUBITS_PER_NODE", "28")
    monkeypatch.setenv("SLURM_DEVICE_INFO", '{"qubits":[0,1]}')
    monkeypatch.setenv("SLURM_WORK_ROOT", "/shared/oqtopus/jobs")
    monkeypatch.setenv("SLURM_BATCH_SCRIPT", "/shared/oqtopus/run.sh")
    monkeypatch.setenv(
        "SLURM_WORKER_SCRIPT",
        "/shared/oqtopus/run_qulacs_mpi.py",
    )
    config_path = Path(__file__).parents[2] / "config" / "slurm_simulator_config.yaml"

    config = load_config(str(config_path))
    container = DiContainer(**config["di_container"])

    fetcher = container.get("job_fetcher")
    simulator_step = container.get("slurm_simulator_step")
    exception_handler = container.get("pipeline_exception_handler")
    device_fetcher = container.get("device_fetcher")
    pipeline = PipelineBuilder.build(config["pipeline_manager"], container)

    assert fetcher._execution_repository is simulator_step._execution_repository
    assert exception_handler._execution_repository is simulator_step._execution_repository
    assert device_fetcher._slurm_client is simulator_step._slurm_client
    assert exception_handler._slurm_client is simulator_step._slurm_client
    assert exception_handler._work_root == simulator_step._work_root
    assert getattr(simulator_step, "_qubits_per_node") == 28
    assert getattr(simulator_step, "_imaginary_tolerance") == 1e-10
    assert fetcher._batch_script == Path("/shared/oqtopus/run.sh")
    assert fetcher._worker_script == Path(
        "/shared/oqtopus/run_qulacs_mpi.py"
    )
    assert simulator_step._batch_script == Path("/shared/oqtopus/run.sh")
    assert simulator_step._worker_script == Path(
        "/shared/oqtopus/run_qulacs_mpi.py"
    )
    assert pipeline.job_buffer is container.get("buffer")
    assert config["pipeline_manager"]["pipelines"][0]["steps"] == [
        "simulator_lifecycle_step",
        "estimator_step",
        "buffer",
        "slurm_simulator_step",
    ]
    assert container.get("estimator_step")._skip_direct_estimation is True


def test_slurm_config_wires_oidc_into_all_cloud_clients(monkeypatch):
    """All three Cloud clients (Job, Device, SLURM execution) must receive the
    OIDC configuration. Before this, slurm_simulator_config.yaml passed only
    API-key settings, so in an OIDC deployment the Job/Device repos ran with an
    empty token config and the execution repo stayed on x-api-key."""
    monkeypatch.setenv("JOB_REPOSITORY_AUTH_MODE", "oidc")
    monkeypatch.setenv("DEVICE_REPOSITORY_AUTH_MODE", "oidc")
    monkeypatch.setenv("OIDC_TOKEN_URL", "https://idp.example/token")
    monkeypatch.setenv("OIDC_CLIENT_ID", "engine")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "secret")
    monkeypatch.setenv("OIDC_SCOPE", "provider.write")
    config_path = Path(__file__).parents[2] / "config" / "slurm_simulator_config.yaml"

    registry = load_config(str(config_path))["di_container"]["registry"]

    for name in ("job_repository", "device_repository", "execution_repository"):
        entry = registry[name]
        assert entry["auth_mode"] == "oidc", name
        assert entry["oidc_token_url"] == "https://idp.example/token", name
        assert entry["oidc_client_id"] == "engine", name
        assert entry["oidc_client_secret"] == "secret", name
        assert entry["oidc_scope"] == "provider.write", name
