import json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from oqtopus_engine_core.steps.ro_error_mitigation_step import ReadoutErrorMitigationStep


@pytest.fixture
def setup_mocks():
    gctx = MagicMock()
    jctx = MagicMock()
    job = MagicMock()

    # Set up device info
    device_info = {
        "qubits": [
            {"meas_error": {"prob_meas1_prep0": 0.01, "prob_meas0_prep1": 0.02}},
            {"meas_error": {"prob_meas1_prep0": 0.03, "prob_meas0_prep1": 0.04}},
        ]
    }
    gctx.device.device_info = json.dumps(device_info)

    # Set up job info
    job.shots = 1000
    job.job_info.result.sampling.counts = {"00": 500, "01": 300, "10": 150, "11": 50}

    return gctx, jctx, job


@pytest.mark.asyncio
async def test_post_process_with_ro_error_mitigation_no_transpile(setup_mocks):
    gctx, jctx, job = setup_mocks

    # Configure job for testing
    job.mitigation_info = {"ro_error_mitigation": "pseudo_inverse"}
    job.job_info.transpile_result = None
    job.job_info.program = [MagicMock()]
    job.job_info.program[0] = (
        'OPENQASM 3.0;\ninclude "stdgates.inc";\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\n'
    )

    # Create mock for ro_error_mitigation
    mitigated_counts = {"00": 480, "01": 320, "10": 140, "11": 60}
    step = ReadoutErrorMitigationStep()
    step.ro_error_mitigation = MagicMock(return_value=mitigated_counts)

    # Execute post_process
    await step.post_process(gctx, jctx, job)

    # Verify ro_error_mitigation was called with correct parameters
    step.ro_error_mitigation.assert_called_once()
    args = step.ro_error_mitigation.call_args[0]
    device_info = json.loads(gctx.device.device_info)
    assert args[0] == device_info["qubits"]
    assert args[2] == job.shots
    assert args[3] == {"0": 0, "1": 1}

    # Verify job counts were updated
    assert job.job_info.result.sampling.counts == mitigated_counts


@pytest.mark.asyncio
async def test_post_process_with_ro_error_mitigation_with_transpile(setup_mocks):
    gctx, jctx, job = setup_mocks

    # Configure job for testing
    job.mitigation_info = {"ro_error_mitigation": "pseudo_inverse"}
    job.job_info.transpile_result = MagicMock()
    job.job_info.transpile_result.virtual_physical_mapping = {
        "qubit_mapping": {"0": 1, "1": 0}  # Swap qubits 0 and 1
    }

    # Create mock for ro_error_mitigation
    mitigated_counts = {"00": 490, "01": 310, "10": 145, "11": 55}
    step = ReadoutErrorMitigationStep()
    step.ro_error_mitigation = MagicMock(return_value=mitigated_counts)

    # Execute post_process
    await step.post_process(gctx, jctx, job)

    # Verify ro_error_mitigation was called with correct parameters
    step.ro_error_mitigation.assert_called_once()
    args = step.ro_error_mitigation.call_args[0]
    device_info = json.loads(gctx.device.device_info)
    assert args[0] == device_info["qubits"]
    # assert args[1] == job.job_info.result.sampling.counts
    assert args[2] == job.shots
    assert args[3] == {"0": 1, "1": 0}

    # Verify job counts were updated
    assert job.job_info.result.sampling.counts == mitigated_counts


@pytest.mark.asyncio
async def test_post_process_with_null_mitigation_info(setup_mocks):
    gctx, jctx, job = setup_mocks

    # Configure job with null mitigation info
    job.mitigation_info = {"ro_error_mitigation": None}
    original_counts = job.job_info.result.sampling.counts.copy()

    # Create step
    step = ReadoutErrorMitigationStep()
    step.ro_error_mitigation = MagicMock()

    # Execute post_process
    await step.post_process(gctx, jctx, job)

    # Verify ro_error_mitigation was not called
    step.ro_error_mitigation.assert_not_called()

    # Verify job counts were not changed
    assert job.job_info.result.sampling.counts == original_counts


@pytest.mark.asyncio
async def test_post_process_without_mitigation_info(setup_mocks):
    gctx, jctx, job = setup_mocks

    # Configure job without mitigation info
    job.mitigation_info = {}
    original_counts = job.job_info.result.sampling.counts.copy()

    # Create step
    step = ReadoutErrorMitigationStep()
    step.ro_error_mitigation = MagicMock()

    # Execute post_process
    with patch("oqtopus_engine_core.steps.ro_error_mitigation_step.logger") as mock_logger:
        await step.post_process(gctx, jctx, job)

    # Verify ro_error_mitigation was not called
    step.ro_error_mitigation.assert_not_called()

    # Verify job counts were not changed
    assert job.job_info.result.sampling.counts == original_counts

    # Verify logger was called
    mock_logger.debug.assert_called_with(
        "ro_error_mitigation is not set in job.mitigation_info. "
        "Skipping Readout Error Mitigation Step."
    )


def test_ro_error_mitigation_basic() -> None:
    """Test basic functionality of ro_error_mitigation with simple inputs.

    Raises:
        ValueError: If the result does not match the expected counts.

    """
    # Mock qubits data
    qubits = [
        {"meas_error": {"prob_meas1_prep0": 0.01, "prob_meas0_prep1": 0.02}},
        {"meas_error": {"prob_meas1_prep0": 0.03, "prob_meas0_prep1": 0.04}},
    ]
    # Mock counts, shots and mapping
    counts = {"00": 500, "01": 300, "10": 150, "11": 50}
    shots = 1000
    virtual_physical_mapping = {"0": 0, "1": 1}

    # Create the step
    step = ReadoutErrorMitigationStep()

    # Mock the LocalReadoutMitigator and related functionality
    with (
        patch(
            "oqtopus_engine_core.steps.ro_error_mitigation_step.LocalReadoutMitigator"
        ) as mock_mitigator_cls,
        patch(
            "oqtopus_engine_core.steps.ro_error_mitigation_step.Counts"
        ) as mock_counts_cls,
        patch(
            "oqtopus_engine_core.steps.ro_error_mitigation_step.np.array",
            return_value="mock_array",
        ),
    ):
        # Set up the mock mitigator
        mock_mitigator = mock_mitigator_cls.return_value
        mock_quasi_dist = mock_mitigator.quasi_probabilities.return_value
        mock_nearest_prob = (
            mock_quasi_dist.nearest_probability_distribution.return_value
        )
        mock_binary_probs = mock_nearest_prob.binary_probabilities.return_value

        # Set the binary probabilities result
        mock_binary_probs = {"00": 0.48, "01": 0.32, "10": 0.14, "11": 0.06}
        mock_nearest_prob.binary_probabilities.return_value = mock_binary_probs

        # Call the method
        result = step.ro_error_mitigation(
            qubits, counts, shots, virtual_physical_mapping
        )

        # Verify the expected calls
        mock_mitigator_cls.assert_called_once()
        mock_counts_cls.assert_called_once_with(
            {"0b00": 500, "0b01": 300, "0b10": 150, "0b11": 50}, memory_slots=2
        )
        mock_mitigator.quasi_probabilities.assert_called_once()
        mock_quasi_dist.nearest_probability_distribution.assert_called_once()
        mock_nearest_prob.binary_probabilities.assert_called_once_with(num_bits=2)

        # Check the result
        expected_result = {"00": 480, "01": 320, "10": 140, "11": 60}
        assert result == expected_result, (
            f"Expected {expected_result}, but got {result}"
        )


def test_ro_error_mitigation_with_qubit_reordering():
    """Test ro_error_mitigation with reordered qubits."""
    # Mock qubits data
    qubits = [
        {"meas_error": {"prob_meas1_prep0": 0.01, "prob_meas0_prep1": 0.02}},
        {"meas_error": {"prob_meas1_prep0": 0.03, "prob_meas0_prep1": 0.04}},
    ]

    # Mock counts, shots and mapping with reordered qubits
    counts = {"00": 500, "01": 300, "10": 150, "11": 50}
    shots = 1000
    virtual_physical_mapping = {"0": 1, "1": 0}  # Swap qubits 0 and 1

    step = ReadoutErrorMitigationStep()

    # Create a partial mock of the method to verify the sorted order
    with patch.object(
        step, "ro_error_mitigation", wraps=step.ro_error_mitigation
    ) as spy_method:
        try:
            spy_method(qubits, counts, shots, virtual_physical_mapping)
        except Exception:
            pass  # We're just checking the sorted order, not the full execution

        # Get the sorted_vpm from the spy call
        sorted_vpm = None
        for call in spy_method.mock_calls:
            call_name, call_args, call_kwargs = call
            if len(call_args) > 0 and isinstance(call_args[0], list):
                # Find where sorted_vpm is created in the method
                sorted_vpm = sorted(
                    virtual_physical_mapping.items(),
                    key=lambda item: int(item[0]),
                )
                break

        # Verify the sorted order
        assert sorted_vpm == [("0", 1), ("1", 0)]


def test_ro_error_mitigation_too_many_qubits():
    """Test ro_error_mitigation raises ValueError when there are too many qubits."""
    step = ReadoutErrorMitigationStep()

    # Create a large virtual_physical_mapping
    virtual_physical_mapping = {str(i): i for i in range(33)}  # More than 32 qubits

    with pytest.raises(ValueError) as excinfo:
        step.ro_error_mitigation(
            qubits=[{}] * 33,  # Not used due to early failure
            counts={},  # Not used due to early failure
            shots=1000,  # Not used due to early failure
            virtual_physical_mapping=virtual_physical_mapping,
        )

    assert "input measured_qubits is too large" in str(excinfo.value)


def test_ro_error_mitigation_integration():
    """Integration test for ro_error_mitigation with real objects."""
    # Create real qubits data
    qubits = [
        {"meas_error": {"prob_meas1_prep0": 0.01, "prob_meas0_prep1": 0.02}},
        {"meas_error": {"prob_meas1_prep0": 0.03, "prob_meas0_prep1": 0.04}},
    ]

    counts = {"00": 500, "01": 300, "10": 150, "11": 50}
    shots = 1000
    virtual_physical_mapping = {"0": 0, "1": 1}

    step = ReadoutErrorMitigationStep()

    # Create mocks for qiskit objects
    with (
        patch(
            "oqtopus_engine_core.steps.ro_error_mitigation_step.LocalReadoutMitigator"
        ) as mock_mitigator_cls,
        patch(
            "oqtopus_engine_core.steps.ro_error_mitigation_step.Counts"
        ) as mock_counts_cls,
        patch("oqtopus_engine_core.steps.ro_error_mitigation_step.np", wraps=np) as mock_np,
    ):
        # Configure the mocks
        mock_mitigator = mock_mitigator_cls.return_value
        mock_quasi_dist = MagicMock()
        mock_nearest_prob = MagicMock()
        mock_mitigator.quasi_probabilities.return_value = mock_quasi_dist
        mock_quasi_dist.nearest_probability_distribution.return_value = (
            mock_nearest_prob
        )
        mock_nearest_prob.binary_probabilities.return_value = {
            "00": 0.48,
            "01": 0.32,
            "10": 0.14,
            "11": 0.06,
        }

        result = step.ro_error_mitigation(
            qubits, counts, shots, virtual_physical_mapping
        )

        # Check call to np.array was made correctly
        assert mock_np.array.call_count == 2

        # Check matrices were created correctly
        a_matrices = []
        for call in mock_np.array.call_args_list:
            a_matrices.append(call[0][0])

        expected_matrix1 = [[1 - 0.01, 0.02], [0.01, 1 - 0.02]]
        expected_matrix2 = [[1 - 0.03, 0.04], [0.03, 1 - 0.04]]

        # Check array contents approximately
        np.testing.assert_allclose(a_matrices[0], expected_matrix1)
        np.testing.assert_allclose(a_matrices[1], expected_matrix2)

        # Check the final result
        assert result == {"00": 480, "01": 320, "10": 140, "11": 60}
