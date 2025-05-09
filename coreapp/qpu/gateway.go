package qpu

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/common"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	qint "github.com/oqtopus-team/oqtopus-engine/coreapp/gen/qpu/qpu_interface/v1"
	api "github.com/oqtopus-team/oqtopus-engine/coreapp/oas/gen/providerapi"
	"go.uber.org/zap"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

type GatewayAgent interface {
	Setup() error
	CallDeviceInfo() (*core.DeviceInfo, error)
	CallJob(core.Job) error
	Reset()
	Close()

	GetAddress() string
}

type DefaultGatewayAgentSetting struct {
	GatewayHost string `toml:"gateway_host"`
	GatewayPort string `toml:"gateway_port"`
	APIEndpoint string `toml:"api_endpoint"`
	APIKey      string `toml:"api_key"`
	DeviceId    string `toml:"device_id"`
}

func NewDefaultGatewayAgentSetting() DefaultGatewayAgentSetting {
	return DefaultGatewayAgentSetting{
		GatewayHost: "localhost",
		GatewayPort: "50051",
		APIEndpoint: "https://localhost",
		APIKey:      "your_api_key",
		DeviceId:    "your_device_id",
	}
}

type DefaultGatewayAgent struct {
	setting        DefaultGatewayAgentSetting
	gatewayAddress string
	gatewayConn    *grpc.ClientConn
	apiConn        *grpc.ClientConn
	gatewayClient  qint.QpuServiceClient
	apiClient      *api.Client
	ctx            context.Context

	lastDeviceInfo *core.DeviceInfo
}

func NewGatewayAgent() *DefaultGatewayAgent {
	return &DefaultGatewayAgent{}
}

func (q *DefaultGatewayAgent) Setup() (err error) {
	s, ok := core.GetComponentSetting("gateway")
	if !ok {
		msg := "gateway setting is not found"
		return fmt.Errorf(msg)
	}
	zap.L().Debug(fmt.Sprintf("gateway setting:%v", s))
	// TODO: fix this adhoc
	// partial setting is not allowed...
	mapped, ok := s.(map[string]interface{})
	if !ok {
		q.setting = NewDefaultGatewayAgentSetting()
	} else {
		q.setting = DefaultGatewayAgentSetting{
			GatewayHost: mapped["gateway_host"].(string),
			GatewayPort: mapped["gateway_port"].(string),
			APIEndpoint: mapped["api_endpoint"].(string),
			APIKey:      mapped["api_key"].(string),
			DeviceId:    mapped["device_id"].(string),
		}
	}
	err = nil
	address, err := common.ValidAddress(q.setting.GatewayHost, q.setting.GatewayPort)
	if err != nil {
		return err
	}
	q.gatewayAddress = address

	ss := common.NewSecuritySource(q.setting.APIKey)
	loggingTransport := &loggingRoundTripper{
		next: http.DefaultTransport,
	}
	httpClient := &http.Client{
		Transport: loggingTransport,
	}
	apiClient, err := api.NewClient(q.setting.APIEndpoint, ss, api.WithClient(httpClient))
	if err != nil {
		zap.L().Error("Failed to create API client with logging transport", zap.String("endpoint", q.setting.APIEndpoint), zap.Error(err))
		return fmt.Errorf("failed to create API client: %w", err)
	}
	zap.L().Info("API Client created with local logging transport", zap.String("endpoint", q.setting.APIEndpoint))
	q.apiClient = apiClient

	q.Reset()
	return nil
}

func (q *DefaultGatewayAgent) CallDeviceInfo() (*core.DeviceInfo, error) {
	resGDI, err := q.gatewayClient.GetDeviceInfo(q.ctx, &qint.GetDeviceInfoRequest{})
	if err != nil {
		q.Reset()
		zap.L().Error(fmt.Sprintf("failed to get device info from %s/reason:%s", q.gatewayAddress, err))
		return &core.DeviceInfo{}, err
	}
	di := resGDI.GetBody()
	zap.L().Debug(fmt.Sprintf(
		"DeviceID:%s, ProviderID:%s, Type:%s, MaxQubits:%d, MaxShots:%d, DevicedInfo:%s, CalibratedAt:%s",
		di.DeviceId, di.ProviderId, di.Type, di.MaxQubits, di.MaxShots, di.DeviceInfo, di.CalibratedAt))
	resSS, err := q.gatewayClient.GetServiceStatus(q.ctx, &qint.GetServiceStatusRequest{})
	if err != nil {
		q.Reset()
		zap.L().Error(fmt.Sprintf("failed to get service status from %s/reason:%s", q.gatewayAddress, err))
		return &core.DeviceInfo{}, err
	}
	ds := mapServiceStatusToDeviceStatus(resSS.GetServiceStatus())

	cd := &core.DeviceInfo{
		DeviceName:         di.DeviceId,
		ProviderName:       di.ProviderId,
		Type:               di.Type,
		Status:             ds,
		MaxQubits:          int(di.MaxQubits),
		MaxShots:           int(di.MaxShots),
		DeviceInfoSpecJson: di.DeviceInfo,
		CalibratedAt:       di.CalibratedAt,
	}
	q.callDeviceAPIOnChange(cd)
	return cd, nil
}

func mapServiceStatusToDeviceStatus(ss qint.ServiceStatus) core.DeviceStatus {
	switch ss {
	case qint.ServiceStatus_SERVICE_STATUS_ACTIVE:
		return core.Available
	case qint.ServiceStatus_SERVICE_STATUS_INACTIVE:
		return core.Unavailable
	case qint.ServiceStatus_SERVICE_STATUS_MAINTENANCE:
		return core.QueuePaused
	default:
		zap.L().Error(fmt.Sprintf("unknown service status %d, treating as Unavailable", ss))
		return core.Unavailable // Default to Unavailable for unknown status
	}
}

func (q *DefaultGatewayAgent) CallJob(j core.Job) error {
	var qasmToBeSent string
	if j.JobData().TranspiledQASM == "" {
		qasmToBeSent = j.JobData().QASM
	} else {
		qasmToBeSent = j.JobData().TranspiledQASM
	}

	zap.L().Debug(fmt.Sprintf("Sending a job to QPU/"+
		"JobID:%s, Shots:%d,QASM:%s", j.JobData().ID, j.JobData().Shots, qasmToBeSent))
	startTime := time.Now()
	resp, err := q.gatewayClient.CallJob(q.ctx, &qint.CallJobRequest{
		JobId:   j.JobData().ID,
		Shots:   uint32(j.JobData().Shots),
		Program: qasmToBeSent,
	})
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to call the job in %s/reason:%s", q.gatewayAddress, err))
		return err
	}
	endTime := time.Now()
	// TODO: fix this SRP violation
	switch resp.GetStatus() {
	case qint.JobStatus_JOB_STATUS_SUCCESS:
		j.JobData().Status = core.SUCCEEDED
	case qint.JobStatus_JOB_STATUS_FAILURE, qint.JobStatus_JOB_STATUS_INACTIVE:
		j.JobData().Status = core.FAILED
	default:
		msg := fmt.Sprintf("unknown status %d", resp.GetStatus())
		zap.L().Error(msg)
		return fmt.Errorf(msg)
	}
	zap.L().Debug(fmt.Sprintf("JobID:%s, Status:%s", j.JobData().ID, j.JobData().Status))

	r := j.JobData().Result
	r.Counts = resp.Result.Counts
	r.Message = resp.Result.Message
	r.ExecutionTime = endTime.Sub(startTime)

	zap.L().Debug(fmt.Sprintf("JobID:%s, Counts:%v, Message:%s, ExecutionTime:%s",
		j.JobData().ID, r.Counts, r.Message, r.ExecutionTime))
	return nil
}

func (q *DefaultGatewayAgent) Reset() {
	q.Close()
	q.ctx = context.Background()
	conn, connErr := grpc.NewClient(q.gatewayAddress, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if connErr != nil {
		// connErr is not returned because it is not a main error of this function
		zap.L().Error(fmt.Sprintf("failed to make connection to %s/reason:%s", q.gatewayAddress, connErr))
		return
	}
	q.gatewayConn = conn
	q.gatewayClient = qint.NewQpuServiceClient(conn)
	q.lastDeviceInfo = nil
	zap.L().Debug(fmt.Sprintf("GatewayAgent is ready to use %s", q.gatewayAddress))
	return
}

func (q *DefaultGatewayAgent) Close() {
	if q.gatewayConn != nil {
		_ = q.gatewayConn.Close()
	}
}

func (q *DefaultGatewayAgent) GetAddress() string {
	return q.gatewayAddress
}

func (q *DefaultGatewayAgent) callDeviceAPIOnChange(newDI *core.DeviceInfo) { // Renamed function definition
	updated := false
	if hasStatusChanged(q.lastDeviceInfo, newDI) {
		if err := q.updateDeviceStatus(newDI.Status); err != nil {
			zap.L().Error(fmt.Sprintf("failed to update device status/reason:%s", err))
		} else {
			updated = true
		}
	}
	if hasDeviceInfoChanged(q.lastDeviceInfo, newDI) {
		if err := q.updateDeviceInfo(newDI); err != nil {
			zap.L().Error(fmt.Sprintf("failed to update device info/reason:%s", err))
		} else {
			updated = true
		}
	}
	if hasDeviceChanged(q.lastDeviceInfo, newDI) {
		if err := q.updateDevice(newDI); err != nil {
			zap.L().Error(fmt.Sprintf("failed to update device/reason:%s", err))
		} else {
			updated = true
		}
	}
	if updated {
		q.lastDeviceInfo = newDI
	} else {
		zap.L().Debug("no updated device info")
	}
}

func (q *DefaultGatewayAgent) updateDeviceStatus(st core.DeviceStatus) error {
	apiSt := toDeviceDeviceStatusUpdateStatus(st)
	req := api.NewOptDevicesDeviceStatusUpdate(
		api.DevicesDeviceStatusUpdate{Status: apiSt})
	params := api.PatchDeviceStatusParams{
		DeviceID: q.setting.DeviceId,
	}
	zap.L().Debug("Attempting to update device status", zap.String("deviceID", params.DeviceID), zap.String("status", string(apiSt)))
	_, patchErr := q.apiClient.PatchDeviceStatus(context.TODO(), req, params)
	if patchErr != nil {
		zap.L().Error("API call to update device status failed",
			zap.String("deviceID", params.DeviceID),
			zap.String("status", string(apiSt)),
			zap.Error(patchErr),
		)
	} else {
		zap.L().Info("Successfully initiated device status update", zap.String("deviceID", params.DeviceID))
	}
	return patchErr
}

func (q *DefaultGatewayAgent) updateDeviceInfo(di *core.DeviceInfo) error {
	caStr, err := parseRFC3339Time(di.CalibratedAt)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to parse time %s/reason:%s", di.CalibratedAt, err))
		return err
	}

	req := api.NewOptDevicesDeviceInfoUpdate(
		api.DevicesDeviceInfoUpdate{
			DeviceInfo:   api.NewNilString(di.DeviceInfoSpecJson),
			CalibratedAt: api.NewOptNilDateTime(caStr),
		})
	params := api.PatchDeviceInfoParams{
		DeviceID: q.setting.DeviceId,
	}
	zap.L().Debug("Attempting to update device info", zap.String("deviceID", params.DeviceID), zap.Time("calibratedAt", caStr), zap.String("deviceInfoSpec", di.DeviceInfoSpecJson))
	_, patchErr := q.apiClient.PatchDeviceInfo(context.TODO(), req, params)
	if patchErr != nil {
		zap.L().Error("API call to update device info failed",
			zap.String("deviceID", params.DeviceID),
			zap.Time("calibratedAt", caStr),
			zap.String("deviceInfoSpec", di.DeviceInfoSpecJson),
			zap.Error(patchErr),
		)
	} else {
		zap.L().Info("Successfully initiated device info update", zap.String("deviceID", params.DeviceID))
	}
	return patchErr
}

func (q *DefaultGatewayAgent) updateDevice(di *core.DeviceInfo) error {
	req := api.NewOptDevicesUpdateDeviceRequest(
		api.DevicesUpdateDeviceRequest{
			NQubits: api.NewOptNilInt(int(di.MaxQubits)),
		})
	params := api.PatchDeviceParams{
		DeviceID: q.setting.DeviceId,
	}
	zap.L().Debug("Attempting to update device", zap.String("deviceID", params.DeviceID), zap.Int("maxQubits", di.MaxQubits))
	_, patchErr := q.apiClient.PatchDevice(context.TODO(), req, params)
	if patchErr != nil {
		zap.L().Error("API call to update device failed",
			zap.String("deviceID", params.DeviceID),
			zap.Int("maxQubits", di.MaxQubits),
			zap.Error(patchErr),
		)
	} else {
		zap.L().Info("Successfully initiated device update", zap.String("deviceID", params.DeviceID))
	}
	return patchErr
}

func toDeviceDeviceStatusUpdateStatus(ds core.DeviceStatus) api.DevicesDeviceStatusUpdateStatus {
	switch ds {
	case core.Available:
		return api.DevicesDeviceStatusUpdateStatusAvailable
	case core.Unavailable:
		return api.DevicesDeviceStatusUpdateStatusUnavailable
	default:
		zap.L().Error(fmt.Sprintf("unknown device status %d", ds))
		return api.DevicesDeviceStatusUpdateStatusUnavailable
	}
}

// parseRFC3339Time parses a time string in RFC 3339 format (which is a profile of ISO 8601).
func parseRFC3339Time(t string) (time.Time, error) {
	tt, err := time.Parse(time.RFC3339, t)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to parse time %s using RFC3339/reason:%s", t, err))
		return time.Time{}, err
	}
	return tt, nil
}

func hasStatusChanged(oldSt, newSt *core.DeviceInfo) bool {
	if oldSt == nil {
		zap.L().Debug("old device info is nil")
		return true
	}
	if newSt == nil {
		zap.L().Error("new device info is nil")
		return true
	}
	if oldSt.Status != newSt.Status {
		zap.L().Debug("Status is changed")
		return true
	}
	return false
}

func hasDeviceInfoChanged(oldDI, newDI *core.DeviceInfo) bool {
	if oldDI == nil {
		zap.L().Debug("old device info is nil")
		return true
	}
	if newDI == nil {
		zap.L().Error("new device info is nil")
		return true
	}
	// not check status because it is updated in the uploadDIOnChange
	if oldDI.CalibratedAt != newDI.CalibratedAt {
		zap.L().Debug("CalibratedAt is changed")
		return true
	}
	if oldDI.DeviceInfoSpecJson != newDI.DeviceInfoSpecJson {
		zap.L().Debug("DeviceInfoSpecJson is changed")
		return true
	}
	if oldDI.MaxShots != newDI.MaxShots {
		zap.L().Debug("MaxShots is changed")
		return true
	}
	if oldDI.ProviderName != newDI.ProviderName {
		zap.L().Debug("ProviderName is changed")
		return true
	}
	if oldDI.DeviceName != newDI.DeviceName {
		zap.L().Debug("DeviceName is changed")
		return true
	}
	if oldDI.Type != newDI.Type {
		zap.L().Debug("Type is changed")
		return true
	}
	return false
}

func hasDeviceChanged(oldDI, newDI *core.DeviceInfo) bool {
	// Support only MaxQubits for now
	if oldDI == nil {
		zap.L().Debug("old device info is nil")
		return true
	}
	if newDI == nil {
		zap.L().Error("new device info is nil")
		return true
	}
	if oldDI.MaxQubits != newDI.MaxQubits {
		zap.L().Debug("MaxQubits is changed")
		return true
	}
	return false
}

type loggingRoundTripper struct {
	next http.RoundTripper
}

func (lrt *loggingRoundTripper) RoundTrip(req *http.Request) (*http.Response, error) {
	resp, err := lrt.next.RoundTrip(req)
	if err != nil {
		zap.L().Error("API roundtrip failed", zap.String("url", req.URL.String()), zap.Error(err))
		return nil, err
	}

	bodyBytes, readErr := io.ReadAll(resp.Body)
	if readErr != nil {
		zap.L().Error("Failed to read API response body", zap.Error(readErr), zap.Int("statusCode", resp.StatusCode), zap.String("url", req.URL.String()))
		resp.Body.Close()
		return resp, nil
	}
	resp.Body.Close()

	zap.L().Debug("Received API response",
		zap.String("url", req.URL.String()),
		zap.Int("statusCode", resp.StatusCode),
		zap.ByteString("responseBody", bodyBytes),
	)

	resp.Body = io.NopCloser(bytes.NewReader(bodyBytes))
	return resp, nil
}
