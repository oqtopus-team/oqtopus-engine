package apiclient

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/textproto"

	ht "github.com/ogen-go/ogen/http"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	api "github.com/oqtopus-team/oqtopus-engine/coreapp/oas/gen/providerapi"

	"go.uber.org/zap"
)

type SecuritySource struct {
	apiKey string
}

func (s SecuritySource) ApiKeyAuth(ctx context.Context, name api.OperationName) (api.ApiKeyAuth, error) {
	apiKeyAuth := api.ApiKeyAuth{}
	apiKeyAuth.SetAPIKey(s.apiKey)
	return apiKeyAuth, nil
}

type SseApiClient struct {
	client         api.Invoker
	securitySource SecuritySource
}

func getParamsFromSetting() (apiKey string, endpoint string, err error) {
	apiKey = ""
	endpoint = ""
	err = nil
	setting := core.GetGlobalSetting()
	if setting == nil {
		err = fmt.Errorf("failed to get setting: group setting is nil.")
		return
	}

	periodic_task, ok := setting.RunGroupSetting["periodic_tasks"].(map[string]interface{})
	if !ok {
		err = fmt.Errorf("failed to get setting: periodic_tasks is nil.")
		return
	}
	poller, ok := periodic_task["poller"].(map[string]interface{})
	if !ok {
		err = fmt.Errorf("failed to get setting: periodic_tasks.poller is nil.")
		return
	}
	params, ok := poller["params"].(map[string]interface{})
	if !ok {
		err = fmt.Errorf("failed to get setting: periodic_tasks.poller.params is nil.")
		return
	}

	apiKey, ok = params["api_key"].(string)
	if !ok {
		err = fmt.Errorf("failed to get setting: periodic_tasks.poller.params.api_key is nil.")
		return
	}

	endpoint, ok = params["endpoint"].(string)
	if !ok {
		err = fmt.Errorf("failed to get setting: periodic_tasks.poller.params.endpoint is nil.")
		return
	}

	return
}

func NewSseApiClient() (sseApiClient *SseApiClient, err error) {
	apiKey, endpoint, err := getParamsFromSetting()
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to get the setting: %s", err))
		return nil, err
	}

	ss := SecuritySource{apiKey: apiKey}
	client, err := api.NewClient(endpoint, ss)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to make a client: %s", err))
		return nil, err
	}
	sseApiClient = &SseApiClient{
		client:         client,
		securitySource: ss,
	}
	return sseApiClient, nil
}

func (c *SseApiClient) GetSsesrc(jobId string) (string, error) {
	params := api.GetSsesrcParams{
		JobID: jobId,
	}
	res, err := c.client.GetSsesrc(context.Background(), params)
	if err != nil {
		msg := fmt.Sprintf("failed to get the source file of SSE: %s", err)
		zap.L().Error(msg)
		return "", fmt.Errorf(msg)
	}

	switch res.(type) {
	case *api.GetSsesrcOK:
		body, ok := res.(*api.GetSsesrcOK)
		if !ok {
			msg := fmt.Sprintf("failed to the source file of SSE: unexpected response type")
			zap.L().Error(msg)
			return "", fmt.Errorf(msg)
		}
		b, err := io.ReadAll(body)
		if err != nil {
			msg := fmt.Sprintf("failed to read response body. Reason: %s", err)
			zap.L().Error(msg)
			return "", fmt.Errorf(msg)
		}
		zap.L().Debug(string(b))
		return string(b), nil
	default:
		msg := fmt.Sprintf("unexpected response type %T", res)
		zap.L().Error(msg)
		return "", fmt.Errorf(msg)
	}
}

func (c *SseApiClient) PatchSselog(jobId string, body *bytes.Buffer, fileName string, contentType string) (string, error) {
	params := api.PatchSselogParams{
		JobID: jobId,
	}
	mimeHeader := textproto.MIMEHeader{}
	mimeHeader.Add("Content-Type", contentType)
	req := api.JobsUploadSselogRequestMultipart{
		File: ht.MultipartFile{
			Name:   fileName,
			File:   body,
			Size:   int64(body.Len()),
			Header: mimeHeader,
		},
	}

	request := api.NewOptJobsUploadSselogRequestMultipart(req)
	res, err := c.client.PatchSselog(context.Background(), request, params)
	if err != nil {
		msg := fmt.Sprintf("failed to upload the log file of SSE: %s", err)
		zap.L().Error(msg)
		return "", fmt.Errorf(msg)
	}

	switch res.(type) {
	case *api.JobsUploadSselogResponse:
		response, ok := res.(*api.JobsUploadSselogResponse)
		if !ok {
			msg := fmt.Sprintf("failed to the upload the log file of SSE: unexpected response type")
			zap.L().Error(msg)
			return "", fmt.Errorf(msg)
		}
		zap.L().Debug(response.Message)
		return response.Message, nil
	default:
		msg := fmt.Sprintf("unexpected response type %T", res)
		zap.L().Error(msg)
		return "", fmt.Errorf(msg)
	}

}
