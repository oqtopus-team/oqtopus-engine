import pytest

from oqtopus_engine_core.framework.engine import Engine


class DummyPipeline:
    def __init__(self):
        self.started = False

    async def start(self):
        self.started = True


class DummyJobFetcher:
    def __init__(self):
        self.started = False
        self.gctx = None
        self.pipeline = None

    async def start(self):
        self.started = True


class DummyDeviceFetcher:
    def __init__(self):
        self.started = False
        self.gctx = None

    async def start(self):
        self.started = True


class DummyRepository:
    pass


class DummyDiContainer:
    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, name):
        return self.mapping[name]

class DummyGlobalContext:
    def __init__(self):
        self.job_repository = None
        self.device_repository = None


@pytest.mark.asyncio
async def test_engine_start_initializes_components():
    """
    Engine.start should:

    - inject gctx into job_fetcher and device_fetcher
    - inject pipeline into job_fetcher
    - set job_repository and device_repository in gctx
    - start pipeline, job_fetcher, and device_fetcher
    """

    pipeline = DummyPipeline()
    job_fetcher = DummyJobFetcher()
    device_fetcher = DummyDeviceFetcher()

    job_repo = DummyRepository()
    device_repo = DummyRepository()

    dicon = DummyDiContainer(
        {
            "job_fetcher": job_fetcher,
            "job_repository": job_repo,
            "device_fetcher": device_fetcher,
            "device_repository": device_repo,
        }
    )

    engine = Engine.__new__(Engine)

    gctx = DummyGlobalContext()
    gctx.config = {}

    engine._gctx = gctx
    engine._dicon = dicon
    engine._pipeline = pipeline

    await engine.start()

    assert job_fetcher.gctx is gctx
    assert job_fetcher.pipeline is pipeline
    assert device_fetcher.gctx is gctx

    assert gctx.job_repository is job_repo
    assert gctx.device_repository is device_repo

    assert pipeline.started
    assert job_fetcher.started
    assert device_fetcher.started


@pytest.mark.asyncio
async def test_run_components_runs_all():
    """
    _run_components should start all components concurrently.
    """

    class Dummy:
        def __init__(self):
            self.started = False

        async def start(self):
            self.started = True

    a = Dummy()
    b = Dummy()

    engine = Engine.__new__(Engine)

    await engine._run_components([a, b])

    assert a.started
    assert b.started


def test_inject_common_grpc_options_adds_options_to_grpc_components():
    config = {
        "grpc": {"max_message_length": 1234},
        "di_container": {
            "registry": {
                "tranqu_step": {
                    "_target_": "oqtopus_engine_core.steps.TranquStep",
                    "tranqu_address": "localhost:52020",
                },
                "job_repository": {
                    "_target_": "oqtopus_engine_core.repositories.NullJobRepository",
                },
                "custom_override": {
                    "_target_": "oqtopus_engine_core.steps.EstimatorStep",
                    "grpc_options": {"max_message_length": 5678},
                },
            },
        },
    }

    updated = Engine._inject_common_grpc_options(config)
    registry = updated["di_container"]["registry"]

    assert registry["tranqu_step"]["grpc_options"] == {"max_message_length": 1234}
    assert "grpc_options" not in registry["job_repository"]
    assert registry["custom_override"]["grpc_options"] == {"max_message_length": 5678}
    assert "grpc_options" not in config["di_container"]["registry"]["tranqu_step"]
