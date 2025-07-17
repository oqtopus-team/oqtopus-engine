package poller

import (
	"context"
	"fmt"
	"net/http"

	api "github.com/oqtopus-team/oqtopus-engine/coreapp/oas/gen/providerapi"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"go.uber.org/zap"

	"github.com/aws/aws-sdk-go-v2/aws"
)

type ProviderPollClient struct {
	client *api.Client

	count      int
	endpoint   string
	edgeName   string
	deviceName string
}

type providerPollClientParams struct {
	cred       aws.Credentials
	region     string
	count      int
	endPoint   string
	edgeName   string
	deviceName string

	apiKey string
}

func newProviderPollClient(p *providerPollClientParams) (*ProviderPollClient, error) {
	pss := pollerSecuritySource{apiKey: p.apiKey}
	cli, err := api.NewClient(p.endPoint, pss)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to create a client/reason:%s", err))
		return nil, err
	}
	return &ProviderPollClient{
		client:     cli,
		count:      p.count,
		endpoint:   p.endPoint,
		edgeName:   p.edgeName,
		deviceName: p.deviceName,
	}, nil
}

func (c *ProviderPollClient) request() ([]core.Job, error) {
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

func (c *ProviderPollClient) downloadUserProgram(jobID string) (string, error) {
	zap.L().Debug(fmt.Sprintf("requesting download userprogram to %s. EdgeName: %s, DeviceName: %s",
		c.endpoint, c.edgeName, c.deviceName))
	uri := fmt.Sprintf("%s/ssejobs/%s/download-src",
		c.endpoint, jobID)

	dummyClient := http.Client{}
	_, err := dummyClient.Get(uri)
	if err != nil {
		return "", err
	}

	return "dummy_sse", nil
}
