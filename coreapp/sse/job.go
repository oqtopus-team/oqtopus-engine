package sse

import (
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/go-openapi/strfmt"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/sse/apiclient"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/sse/conf"
	"go.uber.org/zap"
)

const SSE_JOB = "sse"

type SSEJob struct {
	jobData         *core.JobData
	UserProgramData string

	jobContext *core.JobContext
}

func (j *SSEJob) New(jd *core.JobData, jc *core.JobContext) core.Job {
	return &SSEJob{
		jobData:    jd,
		jobContext: jc,
	}
}

func (j *SSEJob) PreProcess() {
	if err := j.preProcessImpl(); err != nil {
		zap.L().Error(fmt.Sprintf("failed to pre-process a job(%s). Reason:%s", j.JobData().ID, err.Error()))
		core.SetFailureWithError(j, err)
		return
	}
	return
}

func (j *SSEJob) preProcessImpl() (err error) {
	container := core.GetSystemComponents().Container
	err = nil
	jd := j.JobData()
	err = container.Invoke(
		func(d core.DBManager) error {
			return d.Insert(j)
		})
	if err != nil {
		return
	}

	sseconf := conf.GetSSEConf()
	apiClient, err := apiclient.NewSseApiClient()
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to make a client to get the source file of SSE: %s", err))
		return
	}
	userprogramData, err := downloadUserProgram(jd.ID, sseconf, apiClient)
	if err != nil {
		zap.L().Error(fmt.Sprintf("Failed to download userprogram. Reason:%s", err))
		return
	}
	j.UserProgramData = userprogramData

	// Make tmp dir
	dirPath, inputDirPath, err := makeTmpDir(jd.ID, sseconf.HostPath)
	if err != nil {
		return
	}

	// Make user program file
	err = makeUserProgramFile(jd.ID, j.UserProgramData, inputDirPath, sseconf.UserProgramName)
	if err != nil {
		deleteTmpDir(dirPath, jd.ID)
		return
	}

	return
}

func (j *SSEJob) Process() {
	jd := j.JobData()
	zap.L().Info("Starting SSE execution of Job ID:" + jd.ID)
	startTime := time.Now()
	err := RunSSE(j)
	if err != nil {
		zap.L().Error(fmt.Sprintf("Failed to run SSE %s", err.Error()))
		core.SetFailureWithError(j, err)
		return
	}
	// Status is set in RunSSE
	jd.Result.ExecutionTime = time.Since(startTime)
	jd.Ended = strfmt.DateTime(time.Now())
	zap.L().Debug(fmt.Sprintf("SSE execution of Job ID:%s is finished", jd.ID))
	zap.L().Debug(fmt.Sprintf("No PostProcessing for job(%s)", jd.ID))

	return
}

func (j *SSEJob) PostProcess() {
	return
}

func (j *SSEJob) IsFinished() bool {
	return j.JobData().Status == core.SUCCEEDED || j.JobData().Status == core.FAILED
}

func (j *SSEJob) JobData() *core.JobData {
	return j.jobData
}

func (j *SSEJob) JobType() string {
	return SSE_JOB
}

func (j *SSEJob) JobContext() *core.JobContext {
	return j.jobContext
}

func (j *SSEJob) UpdateJobData(jd *core.JobData) {
	j.jobData = jd
}

func (j *SSEJob) Clone() core.Job {
	return &SSEJob{
		jobData:    j.JobData().Clone(),
		jobContext: j.JobContext(),
	}
}

func downloadUserProgram(jobID string, sseconf *conf.SSEConf, apiClient *apiclient.SseApiClient) (string, error) {
	body, err := apiClient.GetSsesrc(jobID)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to download user program. Reason:%s", err.Error()))
		return "", err
	}
	return body, nil
}

func makeUserProgramFile(jobID string, userprogramData string, inputDirPath string, userProgramName string) (err error) {
	err = nil

	// base64 decode
	decoded, err := base64.StdEncoding.DecodeString(userprogramData)
	if err != nil {
		err = fmt.Errorf("failed to decode user program. Reason:%s", err.Error())
		zap.L().Error(err.Error())
		return
	}

	// write to file
	filePath := filepath.Join(inputDirPath, userProgramName)
	err = os.WriteFile(filePath, decoded, 0600)
	if err != nil {
		err = fmt.Errorf("failed to write user program to file. Reason:%s", err.Error())
		zap.L().Error(err.Error())
		return
	}

	return
}

func makeTmpDir(jobID string, baseDir string) (dirPath string, inputDirPath string, err error) {
	dirPath = ""
	inputDirPath = ""
	err = nil

	dirPath = filepath.Join(baseDir, jobID)

	// make tmp dir for input
	inputDirPath = filepath.Join(dirPath, "in")
	err = os.MkdirAll(inputDirPath, 0700)
	if err != nil {
		err = fmt.Errorf("failed to make temp directory for input. Reason:%s", err.Error())
		zap.L().Error(err.Error())
		return
	}

	// make tmp dir for output
	outputDirPath := filepath.Join(dirPath, "out")
	err = os.MkdirAll(outputDirPath, 0700)
	if err != nil {
		err = fmt.Errorf("failed to make temp directory for output. Reason:%s", err.Error())
		zap.L().Error(err.Error())
		return
	}

	return
}

func deleteTmpDir(dirPath string, jobID string) (err error) {
	err = nil
	err = os.RemoveAll(dirPath)
	if err != nil {
		zap.L().Info(fmt.Sprintf("failed to delete temp directory. Job(%s), Reason:%s", jobID, err.Error()))
		return
	}
	return
}
