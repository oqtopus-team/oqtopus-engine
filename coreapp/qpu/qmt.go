package qpu

import (
	"context"
	"fmt"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/common"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	qint "github.com/oqtopus-team/oqtopus-engine/coreapp/gen/qpu/qpu_interface/v1"
	api "github.com/oqtopus-team/oqtopus-engine/coreapp/oas/gen/providerapi"
	"go.uber.org/zap"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"time"
)

type QMTAgent interface {
	Setup() error
	CallDeviceInfo() (*core.DeviceInfo, error)
	CallJob(core.Job) error
	Reset()
	Close()

	GetAddress() string
}

type DefaultQMTAgentSetting struct {
	QMTHost     string `toml:"qmt_host"`
	QMTPort     string `toml:"qmt_port"`
	APIEndpoint string `toml:"api_endpoint"`
	APIKey      string `toml:"api_key"`
	DeviceId    string `toml:"device_id"`
}

func NewDefaultQMTAgentSetting() DefaultQMTAgentSetting {
	return DefaultQMTAgentSetting{
		QMTHost:     "localhost",
		QMTPort:     "50051",
		APIEndpoint: "localhost",
		APIKey:      "hogehoge",
		DeviceId:    "hogehoge",
	}
}

type DefaultQMTAgent struct {
	setting    DefaultQMTAgentSetting
	qmtAddress string
	qmtConn    *grpc.ClientConn
	apiConn    *grpc.ClientConn
	qmtClient  qint.QpuServiceClient
	apiClient  *api.Client
	ctx        context.Context

	lastDeviceInfo *core.DeviceInfo
}

func NewQMTAgent() *DefaultQMTAgent {
	return &DefaultQMTAgent{}
}

func (q *DefaultQMTAgent) Setup() (err error) {
	s, ok := core.GetComponentSetting("qmt")
	if !ok {
		msg := "qmt setting is not found"
		return fmt.Errorf(msg)
	}
	zap.L().Debug(fmt.Sprintf("qmt setting:%v", s))
	// TODO: fix this adhoc
	// partial setting is not allowed...
	mapped, ok := s.(map[string]interface{})
	if !ok {
		q.setting = NewDefaultQMTAgentSetting()
	} else {
		q.setting = DefaultQMTAgentSetting{
			QMTHost:     mapped["qmt_host"].(string),
			QMTPort:     mapped["qmt_port"].(string),
			APIEndpoint: mapped["api_endpoint"].(string),
			APIKey:      mapped["api_key"].(string),
			DeviceId:    mapped["device_id"].(string),
		}
	}
	err = nil
	address, err := common.ValidAddress(q.setting.QMTHost, q.setting.QMTPort)
	if err != nil {
		return err
	}
	q.qmtAddress = address
	apiClient, err := newAPIClient(q.setting.APIEndpoint, q.setting.APIKey)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to create a new API client/reason:%s", err))
	}
	q.apiClient = apiClient
	q.Reset()
	return nil
}

func (q *DefaultQMTAgent) CallDeviceInfo() (*core.DeviceInfo, error) {
	resGDI, err := q.qmtClient.GetDeviceInfo(q.ctx, &qint.GetDeviceInfoRequest{})
	if err != nil {
		q.Reset()
		zap.L().Error(fmt.Sprintf("failed to get device info from %s/reason:%s", q.qmtAddress, err))
		return &core.DeviceInfo{}, err
	}
	di := resGDI.GetBody()
	zap.L().Debug(fmt.Sprintf(
		"DeviceID:%s, ProviderID:%s, Type:%s, MaxQubits:%d, MaxShots:%d, DevicedInfo:%s, CalibratedAt:%s",
		di.DeviceId, di.ProviderId, di.Type, di.MaxQubits, di.MaxShots, di.DeviceInfo, di.CalibratedAt))
	resSS, err := q.qmtClient.GetServiceStatus(q.ctx, &qint.GetServiceStatusRequest{})
	if err != nil {
		q.Reset()
		zap.L().Error(fmt.Sprintf("failed to get service status from %s/reason:%s", q.qmtAddress, err))
		return &core.DeviceInfo{}, err
	}
	// TODO functionize
	var ds core.DeviceStatus
	ss := resSS.GetServiceStatus()
	switch ss {
	case qint.ServiceStatus_SERVICE_STATUS_ACTIVE:
		ds = core.Available
	case qint.ServiceStatus_SERVICE_STATUS_INACTIVE:
		ds = core.Unavailable
	case qint.ServiceStatus_SERVICE_STATUS_MAINTENANCE:
		ds = core.QueuePaused
	default:
		zap.L().Error(fmt.Sprintf("unknown service status %d", ss))
	}

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
	q.uploadDIOnChange(cd)
	return cd, nil
}

func (q *DefaultQMTAgent) CallJob(j core.Job) error {
	var qasmToBeSent string
	if j.JobData().TranspiledQASM == "" {
		qasmToBeSent = j.JobData().QASM
	} else {
		qasmToBeSent = j.JobData().TranspiledQASM
	}

	startTime := time.Now()
	resp, err := q.qmtClient.CallJob(q.ctx, &qint.CallJobRequest{
		JobId:   j.JobData().ID,
		Shots:   uint32(j.JobData().Shots),
		Program: qasmToBeSent,
	})
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to call the job in %s/reason:%s", q.qmtAddress, err))
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

func (q *DefaultQMTAgent) Reset() {
	q.Close()
	q.ctx = context.Background()
	conn, connErr := grpc.NewClient(q.qmtAddress, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if connErr != nil {
		// connErr is not returned because it is not a main error of this function
		zap.L().Error(fmt.Sprintf("failed to make connection to %s/reason:%s", q.qmtAddress, connErr))
		return
	}
	q.qmtConn = conn
	q.qmtClient = qint.NewQpuServiceClient(conn)
	q.lastDeviceInfo = nil
	zap.L().Debug(fmt.Sprintf("QMTAgent is ready to use %s", q.qmtAddress))
	return
}

func (q *DefaultQMTAgent) Close() {
	if q.qmtConn != nil {
		_ = q.qmtConn.Close()
	}
}

func (q *DefaultQMTAgent) GetAddress() string {
	return q.qmtAddress
}

func (q *DefaultQMTAgent) uploadDIOnChange(newDI *core.DeviceInfo) {
	// TODO: refactor this long function
	updated := false
	if q.lastDeviceInfo == nil || q.lastDeviceInfo.Status != newDI.Status {
		st := toDeviceDeviceStatusUpdateStatus(newDI.Status)
		req := api.NewOptDevicesDeviceStatusUpdate(
			api.DevicesDeviceStatusUpdate{Status: st})
		params := api.PatchDeviceStatusParams{
			DeviceID: q.setting.DeviceId,
		}
		zap.L().Debug(fmt.Sprintf("status update to %s", st))
		res, err := q.apiClient.PatchDeviceStatus(context.TODO(), req, params)
		if err != nil {
			zap.L().Error(fmt.Sprintf("failed to update device status/reason:%s", err))
			return
		}
		zap.L().Debug(fmt.Sprintf("updated device status %v", res))
		updated = true
	}
	if q.lastDeviceInfo == nil || q.lastDeviceInfo.CalibratedAt != newDI.CalibratedAt {
		caStr := strToTime(newDI.CalibratedAt)
		req := api.NewOptDevicesDeviceInfoUpdate(
			api.DevicesDeviceInfoUpdate{
				DeviceInfo:   api.NewNilString(newDI.DeviceInfoSpecJson),
				CalibratedAt: api.NewOptNilDateTime(caStr),
			})
		params := api.PatchDeviceInfoParams{
			DeviceID: q.setting.DeviceId,
		}
		zap.L().Debug(fmt.Sprintf("calibrated at update to %s", caStr))
		res, err := q.apiClient.PatchDeviceInfo(context.TODO(), req, params)
		if err != nil {
			zap.L().Error(fmt.Sprintf("failed to update device info/reason:%s", err))
			return
		}
		zap.L().Debug(fmt.Sprintf("updated device info %v", res))
		updated = true
	}
	if updated {
		q.lastDeviceInfo = newDI
	} else {
		zap.L().Debug("no updated device info")
	}
	return
}

// TODO: unify with poller
type SecuritySource struct {
	apiKey string
}

func (p SecuritySource) ApiKeyAuth(ctx context.Context, name api.OperationName) (api.ApiKeyAuth, error) {
	apiKeyAuth := api.ApiKeyAuth{}
	apiKeyAuth.SetAPIKey(p.apiKey)
	return apiKeyAuth, nil
}

func newAPIClient(endpoint, apiKey string) (*api.Client, error) {
	ss := SecuritySource{apiKey: apiKey}
	cli, err := api.NewClient("https://"+endpoint, ss)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to create a new API client/reason:%s", err))
		return nil, err
	}
	return cli, nil
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

func strToTime(t string) time.Time {
	tt, err := time.Parse("2006-01-02 15:04:05.999999", t)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to parse time %s/reason:%s", t, err))
		return time.Time{}
	}
	return tt
}
