package apiclient

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/textproto"

	ht "github.com/ogen-go/ogen/http"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/config" // Added import
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
	cfg := config.GetCurrentRunConfig()
	if cfg == nil {
		err = fmt.Errorf("failed to get current run config: config is nil")
		zap.L().Error(err.Error())
		return "", "", err
	}

	// Assuming SSE API client uses the same endpoint and key as the Gateway QPU agent
	gatewaySetting := cfg.Gateway              // Get the interface
	apiKey = gatewaySetting.GetAPIKey()        // Use getter method
	endpoint = gatewaySetting.GetAPIEndpoint() // Use getter method

	if apiKey == "" {
		err = fmt.Errorf("API key is empty in gateway settings")
		zap.L().Error(err.Error())
		// Decide if returning error or default is appropriate
	}
	if endpoint == "" {
		err = fmt.Errorf("API endpoint is empty in gateway settings")
		zap.L().Error(err.Error())
		// Decide if returning error or default is appropriate
	}

	// If either key or endpoint is missing, return the collected error (if any)
	if err != nil {
		return "", "", err // Return empty strings and the error
	}

	zap.L().Debug("Retrieved API params from Gateway settings", zap.String("endpoint", endpoint))
	return apiKey, endpoint, nil
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
