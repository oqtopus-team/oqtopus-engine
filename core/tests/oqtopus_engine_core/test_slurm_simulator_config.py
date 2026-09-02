from pathlib import Path

from oqtopus_engine_core.framework import PipelineBuilder
from oqtopus_util.config import load_config
from oqtopus_util.di import DiContainer


def test_slurm_config_wires_shared_runtime_components(monkeypatch):
    monkeypatch.setenv("SLURM_PARTITION", "Batch")
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
    finalizer = container.get("slurm_result_finalize_step")
    exception_handler = container.get("pipeline_exception_handler")
    device_fetcher = container.get("device_fetcher")
    pipeline = PipelineBuilder.build(config["pipeline_manager"], container)

    assert fetcher._execution_repository is simulator_step._execution_repository
    assert finalizer._execution_repository is simulator_step._execution_repository
    assert exception_handler._execution_repository is simulator_step._execution_repository
    assert device_fetcher._slurm_client is simulator_step._slurm_client
    assert getattr(simulator_step, "_qubits_per_node") == 28
    assert pipeline.job_buffer is container.get("buffer")
    assert config["pipeline_manager"]["pipelines"][0]["steps"] == [
        "slurm_result_finalize_step",
        "tranqu_step",
        "buffer",
        "slurm_simulator_step",
    ]
