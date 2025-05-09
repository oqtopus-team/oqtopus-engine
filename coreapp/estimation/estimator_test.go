//go:build skip
// +build skip

package estimation

import (
	"testing"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	pb "github.com/oqtopus-team/oqtopus-engine/coreapp/estimation/estimation_interface/v1"
	"github.com/stretchr/testify/assert"
)

func TestPreProcess(t *testing.T) {
	s := core.SCWithUnimplementedContainer()
	defer s.TearDown()

	jm, err := core.NewJobManager(&EstimationJob{})
	assert.Nil(t, err)
	assert.NotNil(t, jm)

	jd := core.NewJobData()
	jd.ID = "test_estimation_job"
	jd.QASM = "OPENQASM 3.0;\ninclude \"stdgates.inc\";\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\n"
	jd.JobType = "estimation_job"
	jd.Info = `[{"pauli":"X0 X1","coeff":1.5},{"pauli":"Y0 Z1","coeff":1.2}]`
	jd.Transpiler = &core.TranspilerConfig{}
	jc, err := core.NewJobContext()
	assert.Nil(t, err)

	job, err := jm.NewJobFromJobData(jd, jc)
	assert.Nil(t, err)

	ej := job.(*EstimationJob)

	// expect these fields will be filled in transpile phase
	ej.jobData.Result.TranspilerInfo.VirtualPhysicalMappingMap = map[uint32]uint32{0: 0, 1: 1}
	ej.jobData.TranspiledQASM = "OPENQASM 3.0;\ninclude \"stdgates.inc\";\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\n"
	//ej.origOperators = "[[\"X 0 X 1\", 1.5], [\"Y 0 Z 1\", 1.2]]"

	ej.PreProcess()
	actualQasms := ej.preprocessedQASMs
	actualGroupedOperators := ej.groupedOperators

	expectQasms := []string{
		"OPENQASM 3.0;\ninclude \"stdgates.inc\";\nbit[2] __c_XX;\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\nrz(pi/2) q[0];\nsx q[0];\nrz(pi/2) q[0];\nrz(pi/2) q[1];\nsx q[1];\nrz(pi/2) q[1];\n__c_XX[0] = measure q[0];\n__c_XX[1] = measure q[1];\n",
		"OPENQASM 3.0;\ninclude \"stdgates.inc\";\nbit[2] __c_ZY;\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\nsx q[0];\nrz(pi/2) q[0];\n__c_ZY[0] = measure q[0];\n__c_ZY[1] = measure q[1];\n",
	}
	expectGroupedOperators := "[[[\"XX\"], [\"ZY\"]], [[1.5], [1.2]]]"

	assert.Nil(t, err)
	assert.Equal(t, expectQasms[0], actualQasms[0])
	assert.Equal(t, expectQasms[1], actualQasms[1])
	assert.Equal(t, expectGroupedOperators, actualGroupedOperators)
}

func TestProcess(t *testing.T) {
	s := core.SCWithUnimplementedContainer()
	defer s.TearDown()

	jm, err := core.NewJobManager(&EstimationJob{})
	assert.Nil(t, err)
	assert.NotNil(t, jm)

	jd := core.NewJobData()
	jd.ID = "test_estimation_job"
	jd.QASM = "OPENQASM 3.0;\ninclude \"stdgates.inc\";\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\n"
	jd.JobType = "estimation_job"
	jc, err := core.NewJobContext()
	assert.Nil(t, err)
	job, err := jm.NewJobFromJobData(jd, jc)
	assert.Nil(t, err)

	ej := job.(*EstimationJob)
	ej.preprocessedQASMs = []string{
		"OPENQASM 3.0;\ninclude \"stdgates.inc\";\nbit[2] __c_XX;\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\nrz(pi/2) q[0];\nsx q[0];\nrz(pi/2) q[0];\nrz(pi/2) q[1];\nsx q[1];\nrz(pi/2) q[1];\n__c_XX[0] = measure q[0];\n__c_XX[1] = measure q[1];\n",
		"OPENQASM 3.0;\ninclude \"stdgates.inc\";\nbit[2] __c_ZY;\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\nsx q[0];\nrz(pi/2) q[0];\n__c_ZY[0] = measure q[0];\n__c_ZY[1] = measure q[1];\n",
	}
	ej.Process()

	assert.Equal(t, len(ej.countsList), 2)
}

func TestEstimationPostProcess(t *testing.T) {
	s := core.SCWithUnimplementedContainer()
	defer s.TearDown()

	jm, err := core.NewJobManager(&EstimationJob{})
	assert.Nil(t, err)
	assert.NotNil(t, jm)

	jd := core.NewJobData()
	jd.ID = "test_estimation_job"
	jd.QASM = "OPENQASM 3.0;\ninclude \"stdgates.inc\";\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\n"
	jd.JobType = "estimation_job"
	jc, err := core.NewJobContext()
	assert.Nil(t, err)

	job, err := jm.NewJobFromJobData(jd, jc)
	assert.Nil(t, err)

	ej := job.(*EstimationJob)

	// Input countsList
	countsList := []*pb.Counts{}
	countsList = append(countsList, &pb.Counts{Counts: map[string]uint32{"00": 425, "01": 75, "10": 85, "11": 415}})
	countsList = append(countsList, &pb.Counts{Counts: map[string]uint32{"00": 500, "01": 0, "10": 0, "11": 500}})

	// Input groupedOperators
	ej.groupedOperators = "[[[\"XX\"], [\"ZY\"]], [[1.5], [1.2]]]"

	actual_expval, actual_stds, err := EstimationPostProcess(ej, countsList)
	assert.Nil(t, err)

	clone := core.Estimation{}
	jd.Result.Estimation = &clone

	jd.Result.Estimation.Exp_value = actual_expval
	jd.Result.Estimation.Stds = actual_stds
	assert.Equal(t, float32(2.22), jd.Result.Estimation.Exp_value)
	assert.Equal(t, float32(0.034779303), jd.Result.Estimation.Stds)
}

func TestEstimationPostProcessMitigation(t *testing.T) {
	s := core.SCWithUnimplementedContainer()
	defer s.TearDown()

	jm, err := core.NewJobManager(&EstimationJob{})
	assert.Nil(t, err)
	assert.NotNil(t, jm)

	jd := core.NewJobData()
	jd.ID = "test_estimation_job"
	jd.QASM = "OPENQASM 3.0;\ninclude \"stdgates.inc\";\nqubit[2] q;\nh q[0];\ncx q[0], q[1];\n"
	jd.JobType = ESTIMATION_JOB
	jd.MitigationInfo = "{ \"ro_error_mitigation\": \"pseudo_inverse\"}" // Changed key
	jd.Result.TranspilerInfo.PhysicalVirtualMapping = map[uint32]uint32{0: 0, 1: 1}
	jc, err := core.NewJobContext()
	assert.Nil(t, err)

	job, err := jm.NewJobFromJobData(jd, jc)
	assert.Nil(t, err)

	ej := job.(*EstimationJob)

	// Input countsList
	countsList := []core.Counts{}
	countsList = append(countsList, core.Counts{"00": 425, "01": 75, "10": 85, "11": 415})
	countsList = append(countsList, core.Counts{"00": 500, "01": 0, "10": 0, "11": 500})
	ej.countsList = countsList

	// Input groupedOperators
	ej.groupedOperators = "[[[\"XX\"], [\"ZY\"]], [[1.5], [1.2]]]"

	ej.PostProcess()

	actual_expval := ej.JobData().Result.Estimation.Exp_value
	actual_stds := ej.JobData().Result.Estimation.Stds

	assert.Equal(t, float32(2.7), actual_expval)
	assert.Equal(t, float32(0), actual_stds)
}
