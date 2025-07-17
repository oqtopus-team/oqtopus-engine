package poller

import (
	"fmt"

	"github.com/google/uuid"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/sampling"
	"go.uber.org/zap"
)

type selfStressPollClient struct {
	count int
}

func newSelfStressPollClient(count int) (*selfStressPollClient, error) {
	return &selfStressPollClient{
		count: count,
	}, nil
}

func (c *selfStressPollClient) request() ([]core.Job, error) {
	zap.L().Debug(fmt.Sprintf("generating %d stress jobs", c.count))
	jobs := make([]core.Job, 0, c.count)
	jm := core.GetJobManager()

	for i := 0; i < c.count; i++ {
		jobID, err := uuid.NewRandom()
		if err != nil {
			return nil, err
		}
		jd := core.NewJobData()
		jd.ID = jobID.String()
		jd.JobType = sampling.SAMPLING_JOB
		jd.Status = core.READY
		jd.Shots = 100
		jd.QASM = "OPENQASM 3;include \"stdgates.inc\";qubit[2] q;bit[2] c;cx q[0],q[1];c = measure q;"
		jd.Transpiler = &core.TranspilerConfig{}

		jc, err := core.NewJobContext()
		if err != nil {
			zap.L().Error(fmt.Sprintf("Failed to create a job context. Reason:%s", err))
			continue
		}

		newJob, err := jm.NewJobFromJobDataWithValidation(jd, jc)
		if err != nil {
			msg := core.SetFailureWithErrorToJobData(jd, err)
			zap.L().Error(fmt.Sprintf("Failed to validate a job. Reason:%s", msg))
			newJob = (&core.UnknownJob{}).New(jd, jc)
		}
		jobs = append(jobs, newJob)
	}

	return jobs, nil
}
