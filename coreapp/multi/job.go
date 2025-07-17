package multi

import "github.com/oqtopus-team/oqtopus-engine/coreapp/core"

type MultiAutoJob struct {
	*core.UnimplementedJob
}

func New(jd *core.JobData, jc *core.JobContext) *MultiAutoJob {
	u := &core.UnimplementedJob{}
	return &MultiAutoJob{
		UnimplementedJob: u.New(jd, jc).(*core.UnimplementedJob),
	}
}
