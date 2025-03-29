//go:build unit
// +build unit

package scheduler

import (
	"fmt"
	"sync"
	"testing"

	"github.com/google/uuid"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/stretchr/testify/assert"
)

var jm *core.JobManager

const FAILED_IN_PRE_PROCESS_JOB = "FAILED_in_pre_process_job"
const FAILED_IN_PROCESS_JOB = "FAILED_in_process_job"
const FAILED_IN_POST_PROCESS_JOB = "FAILED_in_post_process_job"
const SUCCESS_IN_POST_PROCESS_JOB = "success_in_post_process_job"

func TestMain(m *testing.M) {
	jm, _ = core.NewJobManager(
		&core.NormalJob{},
		&FAILEDInPreProcessJob{},
		&FAILEDInProcessJob{},
		&FAILEDInPostProcessJob{},
		&successInPostProcessJob{})
	m.Run()
}

func TestHandleJob(t *testing.T) {
	nsc := &NormalScheduler{}
	s := core.SCWithScheduler(nsc)
	defer s.TearDown()
	err := s.StartContainer()
	assert.Nil(t, err)

	tests := []struct {
		name            string
		job             core.Job
		wantStatusSlice []core.Status
	}{
		{
			name: "handle normal job in ready state",
			job:  testJob(t, core.NORMAL_JOB, core.READY),
			wantStatusSlice: []core.Status{
				core.READY,
				core.RUNNING,
				core.SUCCEEDED,
			},
		},
		{
			name: "handle normal job in FAILED",
			job:  testJob(t, core.NORMAL_JOB, core.FAILED),
			wantStatusSlice: []core.Status{
				core.FAILED,
			},
		},
		{
			name: "handle FAILED in pre-proessing job in ready state",
			job:  testJob(t, FAILED_IN_PRE_PROCESS_JOB, core.READY),
			wantStatusSlice: []core.Status{
				core.READY,
				core.FAILED,
			},
		},
		{
			name: "handle FAILED in pre-proessing job in FAILED state",
			job:  testJob(t, FAILED_IN_PRE_PROCESS_JOB, core.FAILED),
			wantStatusSlice: []core.Status{
				core.FAILED,
			},
		},
		{
			name: "handle FAILED process job with pre-processing",
			job:  testJob(t, FAILED_IN_PROCESS_JOB, core.READY),
			wantStatusSlice: []core.Status{
				core.READY,
				core.RUNNING,
				core.FAILED,
			},
		},
		{
			name: "handle FAILED post-process job with FAILED",
			job:  testJob(t, FAILED_IN_POST_PROCESS_JOB, core.FAILED),
			wantStatusSlice: []core.Status{
				core.FAILED,
			},
		},
		{
			name: "handle FAILED post-process job with pre-processing",
			job:  testJob(t, FAILED_IN_POST_PROCESS_JOB, core.READY),
			wantStatusSlice: []core.Status{
				core.READY,
				core.RUNNING,
				core.FAILED,
			},
		},
		{
			name: "handle success post-process job with pre-processing",
			job:  testJob(t, SUCCESS_IN_POST_PROCESS_JOB, core.READY),
			wantStatusSlice: []core.Status{
				core.READY,
				core.RUNNING,
				core.SUCCEEDED,
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			jobID := tt.job.JobData().ID
			var wg sync.WaitGroup
			wg.Add(1)
			nsc.HandleJobForTest(tt.job, &wg)
			wg.Wait()
			assert.Equal(
				t,
				tt.wantStatusSlice,
				nsc.statusHistory[jobID],
				fmt.Sprintf(
					"expected status slice:%s\n actual status slice:%s\n",
					printStatusSlice(tt.wantStatusSlice),
					printStatusSlice(nsc.statusHistory[jobID])))
		})
	}
}

func testJob(t *testing.T, jobType string, firstStatus core.Status) core.Job {
	jd := core.NewJobData()
	jd.ID = uuid.NewString()
	jd.QASM = "test_qasm"
	jd.Shots = 1000
	jd.Status = firstStatus
	jd.JobType = jobType
	jd.Transpiler = core.DEFAULT_TRANSPILER_CONFIG()
	jc, _ := core.NewJobContext()
	j, err := jm.NewJobFromJobData(jd, jc)
	assert.Nil(t, err)
	return j
}

type FAILEDInPreProcessJob struct {
	*core.UnimplementedJob
}

func (j *FAILEDInPreProcessJob) New(jd *core.JobData, jc *core.JobContext) core.Job {
	u := &core.UnimplementedJob{}
	return &FAILEDInPreProcessJob{
		UnimplementedJob: u.New(jd, jc).(*core.UnimplementedJob),
	}
}

func (j *FAILEDInPreProcessJob) PreProcess() {
	j.JobData().Status = core.FAILED
	return
}

func (j *FAILEDInPreProcessJob) JobType() string {
	return FAILED_IN_PRE_PROCESS_JOB
}

type FAILEDInProcessJob struct {
	*core.UnimplementedJob
}

func (j *FAILEDInProcessJob) New(jd *core.JobData, jc *core.JobContext) core.Job {
	u := &core.UnimplementedJob{}
	return &FAILEDInProcessJob{
		UnimplementedJob: u.New(jd, jc).(*core.UnimplementedJob),
	}
}

func (j *FAILEDInProcessJob) Process() {
	j.JobData().Status = core.FAILED
	return
}

func (j *FAILEDInProcessJob) JobType() string {
	return FAILED_IN_PROCESS_JOB
}

type FAILEDInPostProcessJob struct {
	*core.UnimplementedJob
}

func (j *FAILEDInPostProcessJob) New(jd *core.JobData, jc *core.JobContext) core.Job {
	u := &core.UnimplementedJob{}
	return &FAILEDInPostProcessJob{
		UnimplementedJob: u.New(jd, jc).(*core.UnimplementedJob),
	}
}

func (j *FAILEDInPostProcessJob) Process() {
	j.JobData().Status = core.RUNNING
	return
}

func (j *FAILEDInPostProcessJob) PostProcess() {
	j.JobData().Status = core.FAILED
	return
}

func (j *FAILEDInPostProcessJob) JobType() string {
	return FAILED_IN_POST_PROCESS_JOB
}

type successInPostProcessJob struct {
	*core.UnimplementedJob
}

func (j *successInPostProcessJob) New(jd *core.JobData, jc *core.JobContext) core.Job {
	u := &core.UnimplementedJob{}
	return &successInPostProcessJob{
		UnimplementedJob: u.New(jd, jc).(*core.UnimplementedJob),
	}
}

func (j *successInPostProcessJob) Process() {
	j.JobData().Status = core.SUCCEEDED
	return
}

func (j *successInPostProcessJob) PostProcess() {
	j.JobData().Status = core.SUCCEEDED
	return
}

func (j *successInPostProcessJob) JobType() string {
	return SUCCESS_IN_POST_PROCESS_JOB
}

func printStatusSlice(ss []core.Status) string {
	s := "[\n"
	for _, status := range ss {
		s += fmt.Sprintf("  %v,\n", status)
	}
	return s + "]"
}
