import pytest
from datetime import datetime
from src.oqtopus_engine_core.framework.model import Job

job_info = {"program": ["dummy_program_content"]}


def test_job_repr_and_str_output():
    """Test that repr/str only show IDs for linked jobs and full repr for others."""
    
    # 1. Setup parent and multiple children
    parent_job = Job(
        job_id="parent_001",
        device_id="device_a",
        shots=1000,
        job_type="circuit",
        job_info=job_info,
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="completed",
    )
    
    child_01 = Job(
        job_id="child_001",
        device_id="device_a",
        shots=1000,
        job_type="circuit",
        job_info=job_info,
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="running",
        parent=parent_job
    )

    child_02 = Job(
        job_id="child_002",
        device_id="device_a",
        shots=1000,
        job_type="circuit",
        job_info=job_info,
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="pending",
        parent=parent_job
    )
    
    # Link multiple children to parent
    parent_job.children.extend([child_01, child_02])

    # 2. Verify Child's repr (Quotes removed, parent ID only)
    child_repr = repr(child_01)
    assert "job_id=child_001" in child_repr
    assert "parent=parent_001" in child_repr

    # 3. Verify Parent's repr with multiple children
    # It should show a list of child IDs: ['child_001', 'child_002']
    parent_repr = repr(parent_job)
    assert "job_id=parent_001" in parent_repr
    assert "children=['child_001', 'child_002']" in parent_repr
    
    # 4. Verify __str__ matches __repr__
    assert str(parent_job) == repr(parent_job)


def test_job_repr_with_none_values():
    """Test repr output when optional fields are None."""
    job = Job(
        job_id="job_empty",
        device_id="device_b",
        shots=1,
        job_type="test",
        job_info=job_info,
        transpiler_info={},
        simulator_info={},
        mitigation_info={},
        status="pending",
    )
    
    res = repr(job)
    assert "name=None" in res
    assert "parent=None" not in res
    assert "children=[]" not in res
