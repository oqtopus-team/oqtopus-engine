package core

import (
	"errors"
	"fmt"
	"reflect"
	"time"

	"github.com/go-openapi/strfmt"
	"go.uber.org/zap"
)

var ErrorJobIDConflict = errors.New("jobID is already used")
var jobManager *JobManager

const NORMAL_JOB = "normal"

type Job interface {
	// Job Control
	New(*JobData, *JobContext) Job
	PreProcess()
	Process()
	PostProcess()
	IsFinished() bool

	// Data Access
	JobData() *JobData // Get mutable JobData
	JobType() string
	JobContext() *JobContext
	Clone() Job
}

type JobContext struct {
	*Channels
}

func NewJobContext() (*JobContext, error) {
	s := GetSystemComponents()
	if s == nil {
		return nil, fmt.Errorf("system components is not initialized")
	}
	c := s.Channels
	if c == nil {
		return nil, fmt.Errorf("channels is not initialized")
	}
	return &JobContext{
		Channels: GetSystemComponents().Channels,
	}, nil
}

type JobParam struct {
	JobID      string
	QASM       string
	Shots      int
	Transpiler *TranspilerConfig
	JobType    string
}

type NormalJob struct {
	jobData    *JobData
	jobContext *JobContext
}

func (j *NormalJob) New(jd *JobData, jc *JobContext) Job {
	return &NormalJob{
		jobData:    jd,
		jobContext: jc,
	}
}

func (j *NormalJob) PreProcess() {
	if err := j.preProcessImpl(); err != nil {
		zap.L().Error(fmt.Sprintf("failed to pre-process a job(%s). Reason:%s",
			j.JobData().ID, err.Error()))
		SetFailureWithError(j, err)
		return
	}
	return
}

func (j *NormalJob) preProcessImpl() (err error) {
	err = nil
	jd := j.JobData()
	container := GetSystemComponents().Container
	// TODO refactor this part
	// make jobID pool in syscomponent

	if jd.NeedTranspiling() {
		err = container.Invoke(
			func(t Transpiler) error {
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

func (j *NormalJob) Process() {
	c := GetSystemComponents().Container
	err := c.Invoke(
		func(q QPUManager) error {
			return q.Send(j)
		})
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to send a job(%s) to QPU. Reason:%s", j.JobData().ID, err.Error()))
		j.JobData().Status = FAILED
	}
	zap.L().Debug(fmt.Sprintf("finished to process a job(%s)/status:%s", j.JobData().ID, j.JobData().Status))
}

func (j *NormalJob) PostProcess() {
	return
}

func (j *NormalJob) IsFinished() bool {
	return j.JobData().Status == SUCCEEDED || j.JobData().Status == FAILED
}

func (j *NormalJob) JobData() *JobData {
	return j.jobData
}

func (j *NormalJob) JobType() string {
	return NORMAL_JOB
}

func (j *NormalJob) JobContext() *JobContext {
	return j.jobContext
}

func (j *NormalJob) UpdateJobData(jd *JobData) {
	j.jobData = jd
}

func (j *NormalJob) Clone() Job {
	cloned := &NormalJob{
		jobData:    j.jobData.Clone(),
		jobContext: j.jobContext,
	}
	return cloned
}

// TODO rename to InvalidJob
type UnknownJob struct {
	jobData    *JobData
	jobContext *JobContext
}

func (j *UnknownJob) New(jd *JobData, jc *JobContext) Job {
	return &UnknownJob{
		jobData:    jd,
		jobContext: jc,
	}
}

func (j *UnknownJob) PreProcess() {
	return
}

func (j *UnknownJob) Process() {
	return
}

func (j *UnknownJob) PostProcess() {
	return
}

func (j *UnknownJob) IsFinished() bool {
	return j.JobData().Status == SUCCEEDED || j.JobData().Status == FAILED
}

func (j *UnknownJob) JobData() *JobData {
	return j.jobData
}

func (j *UnknownJob) JobType() string {
	// return unknown job type itself
	return j.jobData.JobType
}

func (j *UnknownJob) JobContext() *JobContext {
	return j.jobContext
}

func (j *UnknownJob) Clone() Job {
	cloned := &UnknownJob{
		jobData:    j.jobData.Clone(),
		jobContext: j.jobContext,
	}
	return cloned
}

func GetJob(id string) (job Job) {
	job = nil
	c := GetSystemComponents().Container
	err := c.Invoke(
		func(d DBManager) error {
			var getErr error
			job, getErr = d.Get(id)
			return getErr
		})
	if err != nil {
		zap.L().Info(fmt.Sprintf("failed to find a job(%s)", id))
		return nil
	}
	return job
}

// remove this function
func DeleteJob(id string) bool {
	c := GetSystemComponents().Container
	err := c.Invoke(
		func(d DBManager) error {
			return d.Delete(id)
		})
	if err != nil {
		zap.L().Info(fmt.Sprintf("failed to delete a job(%s)", id))
		return false
	}
	return true
}

// factory pattern
type JobManager struct {
	acceptableJobs []Job //empty jobs
}

func (j *JobManager) RegisterJob(jobs ...Job) error {
	for _, job := range jobs {
		// check if job is already registered
		for _, t := range j.acceptableJobs {
			if reflect.TypeOf(t) == reflect.TypeOf(job) {
				return fmt.Errorf("job:%s is already registered", job.JobType())
			}

		}
		zap.L().Debug(fmt.Sprintf("registering job type %s", job.JobType()))
		j.acceptableJobs = append(j.acceptableJobs, job)
	}
	return nil
}

func (j *JobManager) AcceptableJobTypes() []string {
	types := []string{}
	for _, job := range j.acceptableJobs {
		types = append(types, job.JobType())
	}
	return types
}

func (j *JobManager) NewJobWithValidation(param *JobParam, jc *JobContext) (Job, error) {
	if param.JobType == "" { // default job type
		param.JobType = NORMAL_JOB
	}
	if err := validateJobParam(param); err != nil {
		zap.L().Info(fmt.Sprintf("failed to validate job param. Reason:%s", err.Error()))
		return nil, err
	}
	return j.NewJob(param, jc)
}

func (j *JobManager) NewJob(param *JobParam, jc *JobContext) (Job, error) {
	jd := NewJobData()
	jd.ID = param.JobID
	jd.QASM = param.QASM
	jd.Shots = param.Shots
	jd.Transpiler = param.Transpiler
	jd.JobType = param.JobType
	return j.NewJobFromJobData(jd, jc)
}

func (j *JobManager) NewJobFromJobDataWithValidation(jd *JobData, jc *JobContext) (Job, error) {
	if jd.JobType == "" { // default job type
		jd.JobType = NORMAL_JOB
	}
	p := &JobParam{
		JobID:      jd.ID,
		QASM:       jd.QASM,
		Shots:      jd.Shots,
		Transpiler: jd.Transpiler,
		JobType:    jd.JobType,
	}
	if err := validateJobParam(p); err != nil {
		zap.L().Info(fmt.Sprintf("failed to validate job data. Reason:%s", err.Error()))
		return nil, err
	}
	return j.NewJobFromJobData(jd, jc)
}

func (j *JobManager) NewJobFromJobData(jd *JobData, jc *JobContext) (Job, error) {
	if jd.JobType == "" { // default job type
		jd.JobType = NORMAL_JOB
	}
	zap.L().Debug(fmt.Sprintf("creating a job from job data. Job ID:%s, Job Type:%s", jd.ID, jd.JobType))
	for _, j := range j.acceptableJobs {
		zap.L().Debug(fmt.Sprintf("checking job type %s", j.JobType()))
		if j.JobType() == jd.JobType {
			// create a new job instance
			t := reflect.TypeOf(j)
			newInstance := reflect.New(t).Elem().Interface()
			job := newInstance.(Job).New(jd, jc)
			return job, nil
		}
	}
	return nil, fmt.Errorf("job type %s is not registered", jd.JobType)
}

func validateJobParam(p *JobParam) (err error) {
	err = nil
	if p.JobID == "" {
		return fmt.Errorf("jobID is empty")
	}

	// TODO validate using PreProcess or Validate method in Job interface
	if p.JobType == NORMAL_JOB {
		if p.Shots <= 0 {
			msg := fmt.Sprintf("shots(%d) must be greater than 0", p.Shots)
			zap.L().Info(msg + fmt.Sprintf("/jobID:%s", p.JobID))
			return fmt.Errorf(msg)
		}
		maxShots := GetSystemComponents().GetDeviceInfo().MaxShots
		if p.Shots > maxShots {
			msg := fmt.Sprintf("shots(%d) is over the limit(%d)",
				p.Shots, maxShots)
			zap.L().Info(msg + fmt.Sprintf("/jobID:%s", p.JobID))
			return fmt.Errorf(msg)
		}
	}
	container := GetSystemComponents().Container
	err = container.Invoke(
		func(t Transpiler) error {
			if p.Transpiler.TranspilerLib == nil {
				return nil // use no transpiler
			}
			if t.IsAcceptableTranspilerLib(*p.Transpiler.TranspilerLib) {
				return nil
			}
			return fmt.Errorf("transpiler lib %s is not acceptable", *p.Transpiler.TranspilerLib)
		})
	if err != nil {
		zap.L().Info(fmt.Sprintf("failed to validate transpiler lib/JobID:%s/reason:%s", p.JobID, err.Error()))
		return err
	}
	return
}

func NewJobManager(jobs ...Job) (*JobManager, error) {
	jm := &JobManager{}
	for _, job := range jobs {
		zap.L().Debug(fmt.Sprintf("registering job type %s", job.JobType()))
		err := jm.RegisterJob(job)
		if err != nil {
			return nil, err
		}
	}
	jobManager = jm
	return jm, nil
}

func GetJobManager() *JobManager {
	return jobManager
}

func SetFailureWithError(j Job, err error) (msg string) {
	jd := j.JobData()
	return SetFailureWithErrorToJobData(jd, err)
}

func SetFailureWithErrorToJobData(jd *JobData, err error) (msg string) {
	msg = err.Error()
	jd.Result.Message = msg
	jd.Status = FAILED
	jd.Ended = strfmt.DateTime(time.Now())
	return msg
}
