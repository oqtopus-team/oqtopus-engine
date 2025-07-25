package sampling

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/mitig"
	"go.uber.org/zap"
)

const SAMPLING_JOB = "sampling"

type SamplingJob struct {
	jobData        *core.JobData
	jobContext     *core.JobContext
	mitigationInfo *mitig.MitigationInfo
}

func (j *SamplingJob) New(jd *core.JobData, jc *core.JobContext) core.Job {
	return &SamplingJob{
		jobData:    jd,
		jobContext: jc,
	}
}

func (j *SamplingJob) PreProcess() {
	if err := j.preProcessImpl(); err != nil {
		zap.L().Error(fmt.Sprintf("failed to pre-process a job(%s). Reason:%s",
			j.JobData().ID, err.Error()))
		core.SetFailureWithError(j, err)
		return
	}
	j.mitigationInfo = mitig.NewMitigationInfoFromJobData(j.JobData())
	return
}

func (j *SamplingJob) preProcessImpl() (err error) {
	err = nil
	jd := j.JobData()
	container := core.GetSystemComponents().Container

	if jd.NeedTranspiling() {
		err = container.Invoke(
			func(t core.Transpiler) error {
				return t.Transpile(j)
			})
		if err != nil {
			zap.L().Error(fmt.Sprintf("failed to transpile a job(%s). Reason:%s", jd.ID, err.Error()))
			return
		}
	} else {
		zap.L().Debug(fmt.Sprintf("skip transpiling a job(%s)/Transpiler:%v",
			jd.ID, jd.Transpiler))
	}
	return
}

func (j *SamplingJob) Process() {
	c := core.GetSystemComponents().Container
	err := c.Invoke(
		func(q core.QPUManager) error {
			return q.Send(j)
		})
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to send a job(%s) to QPU. Reason:%s", j.JobData().ID, err.Error()))
		j.JobData().Status = core.FAILED
	}
	zap.L().Debug(fmt.Sprintf("finished to process a job(%s)/status:%s", j.JobData().ID, j.JobData().Status))
}

func (j *SamplingJob) PostProcess() {
	j.mitigationInfo.Mitigated = true

	shouldMitigate := false
	if j.mitigationInfo.PropertyRaw != nil && json.Valid(j.mitigationInfo.PropertyRaw) {
		var props map[string]string
		if err := json.Unmarshal(j.mitigationInfo.PropertyRaw, &props); err == nil {
			roErrorMitigationValue, ok := props["ro_error_mitigation"] // Changed key and variable name
			// mitig.go と同様に TrimSpace とダブルクォート付きで比較
			if ok && strings.TrimSpace(roErrorMitigationValue) == "\"pseudo_inverse\"" { // Changed variable name
				shouldMitigate = true
			}
		} else {
			zap.L().Warn(fmt.Sprintf("JobID:%s - Failed to unmarshal PropertyRaw in PostProcess: %v", j.JobData().ID, err))
		}
	} else {
		zap.L().Debug(fmt.Sprintf("JobID:%s - PropertyRaw is nil or invalid JSON in PostProcess", j.JobData().ID))
	}

	if shouldMitigate {
		zap.L().Debug(fmt.Sprintf("JobID:%s - Starting pseudo inverse mitigation", j.JobData().ID))
		mitig.PseudoInverseMitigation(j.JobData())
	} else {
		zap.L().Debug(fmt.Sprintf("JobID:%s - Skipping pseudo inverse mitigation", j.JobData().ID))
	}
	return
}

func (j *SamplingJob) IsFinished() bool {
	zap.L().Debug(fmt.Sprintf("checking if job(%s) is finished", j.JobData().ID))
	if j.JobData().Status == core.FAILED {
		zap.L().Debug(fmt.Sprintf("job(%s) is failed", j.JobData().ID))
		return true
	}
	if j.mitigationInfo.NeedToBeMitigated {
		zap.L().Debug(fmt.Sprintf("job(%s) need to be mitigated", j.JobData().ID))
		return j.mitigationInfo.Mitigated
	} else {
		zap.L().Debug(fmt.Sprintf("job(%s) does not need to be mitigated", j.JobData().ID))
		return j.JobData().Status == core.SUCCEEDED
	}
}

func (j *SamplingJob) JobData() *core.JobData {
	return j.jobData
}

func (j *SamplingJob) JobType() string {
	return SAMPLING_JOB
}

func (j *SamplingJob) JobContext() *core.JobContext {
	return j.jobContext
}

func (j *SamplingJob) UpdateJobData(jd *core.JobData) {
	j.jobData = jd
}

func (j *SamplingJob) Clone() core.Job {
	cloned := &SamplingJob{
		jobData:    j.jobData.Clone(),
		jobContext: j.jobContext,
	}
	return cloned
}
