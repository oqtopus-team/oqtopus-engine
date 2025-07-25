//go:build unit
// +build unit

package core

import (
	"fmt"
	"testing"

	"github.com/google/uuid"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/common"
	"github.com/stretchr/testify/assert"
)

func TestJobManager(t *testing.T) {
	s := SCWithUnimplementedContainer()
	defer s.TearDown()
	jm, err := NewJobManager(
		&NormalJob{},
	)
	assert.Nil(t, err)
	assert.NotNil(t, jm)
	as := jm.AcceptableJobTypes()
	assert.Equal(t, len(as), 1)
	assert.Equal(t, as[0], "normal")

	err = jm.RegisterJob(&NormalJob{})
	assert.EqualError(t, err, "job:normal is already registered")
	// TODO other JobType

	as = jm.AcceptableJobTypes()
	assert.Equal(t, len(as), 1)
	assert.Equal(t, as[0], "normal")

	jc, err := NewJobContext()
	assert.Nil(t, err)

	job, err := jm.NewJobFromJobData(
		&JobData{ID: "test"},
		jc,
	)

	assert.Nil(t, err)
	assert.Equal(t, job.JobData().ID, "test")
}

/* TODO: support parsing validation
func TestNewJobFailedForParseError(t *testing.T) {
	s := SCWithValidateErrorContainer()
	defer s.TearDown()
	jm, err := NewJobManager()
	assert.Nil(t, err)
	assert.NotNil(t, jm)
	jm.RegisterJob(&NormalJob{})

	jobID := uuid.NewString()
	p := &JobParam{
		JobID:      jobID,
		QASM:       "",
		Shots:      10000,
		Transpiler: NoTranspiler,
		JobType:    NORMAL_JOB,
	}
	jc, err := NewJobContext()
	assert.Nil(t, err)
	job, err := jm.NewJobWithValidation(p, jc)
	assert.Nil(t, job)
	assert.Equal(t, err, fmt.Errorf(validateErrorMessage))
}
*/

func TestNewJob(t *testing.T) {
	s := SCWithDBContainer()
	defer s.TearDown()

	testQASM, err := common.GetAsset("bell_pair.qasm")
	assert.Nil(t, err)

	jm, err := NewJobManager()
	assert.Nil(t, err)
	assert.NotNil(t, jm)
	jm.RegisterJob(&NormalJob{})

	param := JobParam{
		JobID:      uuid.NewString(),
		QASM:       testQASM,
		Shots:      -1,
		Transpiler: DEFAULT_TRANSPILER_CONFIG(),
	}
	tests := []struct {
		name        string
		param       *JobParam
		wantError   string
		wantJobData *JobData
	}{
		{
			name: "0 shots",
			param: &JobParam{
				JobID:      uuid.NewString(),
				QASM:       testQASM,
				Shots:      0,
				Transpiler: DEFAULT_TRANSPILER_CONFIG(),
			},
			wantError: "shots(0) must be greater than 0",
		},
		{
			name:      "negative shots",
			param:     &param,
			wantError: "shots(-1) must be greater than 0",
		},
		{
			name: "over max shots",
			param: &JobParam{
				JobID:      uuid.NewString(),
				QASM:       testQASM,
				Shots:      MockMaxShots + 1,
				Transpiler: DEFAULT_TRANSPILER_CONFIG(),
			},
			wantError: fmt.Sprintf(
				"shots(%d) is over the limit(%d)",
				MockMaxShots+1, MockMaxShots),
		},
		{
			name: "normal with max shots",
			param: &JobParam{
				JobID:      uuid.NewString(),
				QASM:       testQASM,
				Shots:      MockMaxShots,
				Transpiler: DEFAULT_TRANSPILER_CONFIG(),
			},
			wantError: "",
			wantJobData: &JobData{
				JobType:    NORMAL_JOB,
				Transpiler: DEFAULT_TRANSPILER_CONFIG(),
				QASM:       testQASM,
				Shots:      MockMaxShots,
			},
		},
		{
			name: "normal with 1 shot",
			param: &JobParam{
				JobID:      uuid.NewString(),
				QASM:       testQASM,
				Shots:      1,
				Transpiler: DEFAULT_TRANSPILER_CONFIG(),
			},
			wantError: "",
			wantJobData: &JobData{
				JobType:    NORMAL_JOB,
				Transpiler: DEFAULT_TRANSPILER_CONFIG(),
				QASM:       testQASM,
				Shots:      1,
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			jc, err := NewJobContext()
			assert.Nil(t, err)
			job, err := jm.NewJobWithValidation(tt.param, jc)
			if tt.wantError == "" {
				assert.Nil(t, err)
				tt.wantJobData.ID = tt.param.JobID
				tt.wantJobData.Result = NewResult()
				tt.wantJobData.Created = job.JobData().Created // ignore time
				assert.Equal(t, job.JobData(), tt.wantJobData)
			} else {
				assert.Equal(t, err.Error(), tt.wantError)
			}
		})
	}
}

func TestCloneNormalJob(t *testing.T) {
	s := SCWithUnimplementedContainer()
	defer s.TearDown()
	jm, err := NewJobManager(&NormalJob{})
	assert.Nil(t, err)

	jd := &JobData{
		ID:    "test",
		QASM:  "test_qasm",
		Shots: 1000,
	}
	jc, err := NewJobContext()
	assert.Nil(t, err)
	org, err := jm.NewJobFromJobData(jd, jc)
	assert.Nil(t, err)
	cloned := org.Clone()
	assert.Nil(t, err)
	assert.False(t, cloned == org)
	assert.False(t, cloned.JobData() == org.JobData(),
		"cloned.JobData()=%p, nj.JobData()=%p", cloned.JobData(), org.JobData())
	assert.Equal(t, cloned.JobData().ID, org.JobData().ID)
	assert.Equal(t, cloned.JobData().QASM, org.JobData().QASM)
	assert.Equal(t, cloned.JobData().Shots, org.JobData().Shots)

	org.JobData().ID = "test2"
	assert.NotEqual(t, cloned.JobData().ID, org.JobData().ID)

	org.JobData().Status = RUNNING
	cloned.JobData().Status = SUCCEEDED
	assert.NotEqual(t, cloned.JobData().Status, org.JobData().Status)
}
