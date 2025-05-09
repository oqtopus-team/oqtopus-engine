//go:build skip
// +build skip

package sampling

import (
	"testing"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/stretchr/testify/assert"
)

func TestMain(m *testing.M) {
	m.Run()
}

func TestPostProcessMitigation(t *testing.T) {
	s := core.SCWithUnimplementedContainer()
	defer s.TearDown()

	jm, err := core.NewJobManager(&SamplingJob{})
	assert.Nil(t, err)
	assert.NotNil(t, jm)

	jd := core.NewJobData()
	jd.ID = "test_mitigation_job"
	jd.QASM = "OPENQASM 3.0;\ninclude \"stdgates.inc\";\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\n"
	jd.JobType = SAMPLING_JOB
	jd.MitigationInfo = "{ \"ro_error_mitigation\": \"pseudo_inverse\"}" // Changed key
	jd.Result.Counts = core.Counts{"00": 250, "01": 250, "10": 250, "11": 250}
	jd.Result.TranspilerInfo.PhysicalVirtualMapping = map[uint32]uint32{0: 0, 1: 1}
	jc, err := core.NewJobContext()
	assert.Nil(t, err)

	job, err := jm.NewJobFromJobData(jd, jc)
	assert.Nil(t, err)

	sj := job.(*SamplingJob)
	sj.PostProcess()
	actual := sj.JobData().Result.Counts
	expect := core.Counts{"00": 191, "01": 268, "10": 225, "11": 315}

	assert.Equal(t, expect, actual)
}
