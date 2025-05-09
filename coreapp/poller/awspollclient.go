package poller

import (
	"context"
	"fmt"
	"net/http"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/estimation"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/sampling"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/oas"
	api "github.com/oqtopus-team/oqtopus-engine/coreapp/oas/gen/providerapi"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	multiprog "github.com/oqtopus-team/oqtopus-engine/coreapp/multiprog/manual"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/sse"
	"go.uber.org/zap"

	"github.com/aws/aws-sdk-go-v2/aws"
)

// TODO: remove AWS from the name
type awsPollClient struct {
	client *api.Client

	count      int
	endpoint   string
	edgeName   string // TODO edge id
	deviceName string // TODO device id
}

type awsPollClientParams struct {
	cred       aws.Credentials
	region     string
	count      int
	endPoint   string
	edgeName   string
	deviceName string

	apiKey string
}

type pollerSecuritySource struct {
	apiKey string
}

func (p pollerSecuritySource) ApiKeyAuth(ctx context.Context, name api.OperationName) (api.ApiKeyAuth, error) {
	apiKeyAuth := api.ApiKeyAuth{}
	apiKeyAuth.SetAPIKey(p.apiKey)
	return apiKeyAuth, nil
}

func newAWSPollClient(p *awsPollClientParams) (*awsPollClient, error) {
	pss := pollerSecuritySource{apiKey: p.apiKey}
	cli, err := api.NewClient(p.endPoint, pss)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to create a client/reason:%s", err))
		return nil, err
	}
	return &awsPollClient{
		client:     cli,
		count:      p.count,
		endpoint:   p.endPoint,
		edgeName:   p.edgeName,
		deviceName: p.deviceName,
	}, nil
}

func (c *awsPollClient) request() ([]core.Job, error) {
	zap.L().Debug(fmt.Sprintf("requesting get jobs to %s. EdgeName: %s, DeviceName: %s",
		c.endpoint, c.edgeName, c.deviceName))
	params := api.GetJobsParams{
		DeviceID:   c.deviceName,
		MaxResults: api.NewOptInt(c.count),
		Status:     api.NewOptJobsJobStatus(api.JobsJobStatusSubmitted),
	}
	res0, err := c.client.GetJobs(context.TODO(), params)
	if err != nil {
		msg := fmt.Sprintf("failed to get jobs/reason:%s", err)
		return []core.Job{}, fmt.Errorf(msg)

	}
	jobs := []core.Job{}
	err = nil
	switch res := res0.(type) {
	case *api.GetJobsOKApplicationJSON:
		jobs, err = toJobSlice(*res)
		if err != nil {
			zap.L().Error(fmt.Sprintf("failed to convert to job slice*reason:%s", err))
		}
	default:
		msg := fmt.Sprintf("unexpected response type %T", res0)
		zap.L().Error(msg)
		err = fmt.Errorf(msg)
	}
	return jobs, err
}

// TODO: separate the validation
func toJobSlice(jobDefs []api.JobsJobDef) (jobs []core.Job, err error) {
	jobs = []core.Job{}
	err = nil
	for _, cJob := range jobDefs {
		jd := oas.ConvertFromCloudJob(&cJob)
		// TODO commonize this with testpollclient.go and move to models.go
		jm := core.GetJobManager()
		jc, err := core.NewJobContext()
		if err != nil {
			zap.L().Error(fmt.Sprintf("Failed to create a job context. Reason:%s", err))
			jobs = []core.Job{}
			break
		}
		var newJob core.Job

		switch cJob.JobType {
		case api.JobsJobTypeSampling:
			jd.JobType = sampling.SAMPLING_JOB
		case api.JobsJobTypeEstimation:
			jd.JobType = estimation.ESTIMATION_JOB
		case api.JobsJobTypeSse:
			jd.JobType = sse.SSE_JOB
		case api.JobsJobTypeMultiManual:
			jd.JobType = multiprog.MULTIPROG_MANUAL_JOB
		default:
			msg := fmt.Sprintf("unknown job type %s", cJob.JobType)
			zap.L().Error(msg)
			err = fmt.Errorf(msg)
			jobs = []core.Job{}
			break
		}

		newJob, err = jm.NewJobFromJobDataWithValidation(jd, jc)
		if err != nil {
			msg := core.SetFailureWithErrorToJobData(jd, err)
			zap.L().Error(fmt.Sprintf("Failed to validate a job. Reason:%s", msg))
			newJob = (&core.UnknownJob{}).New(jd, jc)
		} else {
			zap.L().Debug(fmt.Sprintf("Created a job. Job ID:%s created:%s, status:%s, transpiler:%v",
				jd.ID, jd.Created, jd.Status, jd.Transpiler))
		}

		jobs = append(jobs, newJob)
	}
	return jobs, err
}

func (c *awsPollClient) downloadUserProgram(jobID string) (string, error) {
	zap.L().Debug(fmt.Sprintf("requesting download userprogram to %s. EdgeName: %s, DeviceName: %s",
		c.endpoint, c.edgeName, c.deviceName))
	uri := fmt.Sprintf("%s/ssejobs/%s/download-src",
		c.endpoint, jobID)

	// FIXME
	dummyClient := http.Client{}
	_, err := dummyClient.Get(uri)
	if err != nil {
		return "", err
	}

	return "dummy_sse", nil
}
