package router

import (
	"context"
	"encoding/json"
	"fmt"
	"net"

	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/oas"
	ssep "github.com/oqtopus-team/oqtopus-engine/coreapp/sse"
	sseconf "github.com/oqtopus-team/oqtopus-engine/coreapp/sse/conf"
	sse "github.com/oqtopus-team/oqtopus-engine/coreapp/sse/sse_interface/v1"
	"go.uber.org/dig"
	"go.uber.org/zap"
	"google.golang.org/grpc"
)

type GRPCRouter struct {
	sse.UnimplementedSSEServiceServer
	// need the container to request to transpiler
	container *dig.Container
}

// User request data
type UserReqData struct {
	ID         string                 `json:"id" validate:"required"`
	Qasm       string                 `json:"qasm" validate:"required"`
	Shots      uint                   `json:"shots" validate:"required"`
	Transpiler *core.TranspilerConfig `json:"transpiler_info,omitempty"`
}

func (m *GRPCRouter) TranspileAndExec(ctx context.Context, userReq *sse.TranspileAndExecRequest) (*sse.TranspileAndExecResponse, error) {
	zap.L().Info("Received gRPC request of transpiling and executing QPU")
	zap.L().Debug(fmt.Sprintf("Received request: %+v", userReq))
	JobDataJson := userReq.JobDataJson
	res := &sse.TranspileAndExecResponse{}
	res.Status = core.FAILED.String()

	// Validate the request
	if JobDataJson == "" {
		err := fmt.Errorf("Invalid request. Reason: JobDataJson is empty")
		zap.L().Error(err.Error())
		res.Message = "Invalid request. The request data for transpiling is empty."
		zap.L().Debug(fmt.Sprintf("Response: %+v", res))
		return res, nil
	}
	zap.L().Debug(fmt.Sprintf("Received JobDataJson: %s", JobDataJson))

	// Convert JSON to JobData
	j, err := toJob([]byte(JobDataJson))
	if err != nil {
		zap.L().Error(fmt.Sprintf("Failed to convert JSON to JobData. Reason:%s", err))
		res.Message = "Invalid request data for transpiling."
		zap.L().Debug(fmt.Sprintf("Response: %+v", res))
		return res, nil
	}
	jd := j.JobData()

	// validate the number of shots
	err = m.container.Invoke(
		func(q core.QPUManager) error {
			deviceInfo := q.GetDeviceInfo()
			return validateShots(jd.Shots, deviceInfo)
		})
	if err != nil {
		zap.L().Info(fmt.Sprintf("Invalid shots. Reason:%s", err))
		res.Message = fmt.Sprintf("Invalid shots: %s", err)
		zap.L().Debug(fmt.Sprintf("Response: %+v", res))
		return res, nil
	}

	// Validate the QASM
	err = m.container.Invoke(
		func(q core.QPUManager) error {
			return q.Validate(jd.QASM)
		})
	if err != nil {
		zap.L().Info(fmt.Sprintf("Invalid QASM. Reason:%s", err))
		res.Message = fmt.Sprintf("Invalid QASM: %s", err)
		zap.L().Debug(fmt.Sprintf("Response: %+v", res))
		return res, nil
	}

	// TRANSPILE SECTION START
	if jd.Transpiler == nil || useDefaultTranspiler(JobDataJson) {
		jd.Transpiler = oas.DEFAULT_TRANSPILER_CONFIG()
	}
	// Set the transpiler info to response
	transpilerJson, err := json.Marshal(jd.Transpiler)
	if err != nil {
		zap.L().Error(fmt.Sprintf("Failed to marshal transpiler info. Reason:%s", err))
	} else {
		res.TranspilerInfo = string(transpilerJson)
	}

	if jd.Transpiler.NeedTranspiling() {
		zap.L().Info(fmt.Sprintf("Start transpiling for SSE, JobID:%s", jd.ID))
		// Transpile the quantum circuit
		err = m.container.Invoke(
			func(t core.Transpiler) error {
				return t.Transpile(j)
			})
		if err != nil {
			zap.L().Error(fmt.Sprintf("Failed to transpile the quantum circuit. Reason:%s", err))
			res.Message = fmt.Sprintf("Failed to transpile: %s", err)
			zap.L().Debug(fmt.Sprintf("Response: %+v", res))
			return res, nil
		}
		if j.JobData().Status == core.FAILED {
			zap.L().Error("The result status of transpiler is FAILED")
			res.Message = fmt.Sprintf("Failed to transpile")
			zap.L().Debug(fmt.Sprintf("Response: %+v", res))
			return res, nil
		}
	} else {
		zap.L().Info(fmt.Sprintf("Skip transpiling for SSE, JobID:%s", jd.ID))
	}

	// Set the transpiled QASM to response
	res.TranspiledQasm = jd.TranspiledQASM

	// QPU SECTION START
	zap.L().Info(fmt.Sprintf("Start calling qmt for SSE, JobID:%s", jd.ID))
	// Call qmt qpu
	err = m.container.Invoke(
		func(q core.QPUManager) error {
			err = q.Send(j)
			return err
		})
	if err != nil {
		zap.L().Error(fmt.Sprintf("Failed to execute qpu. Reason:%s", err))
		res.Message = "Failed to execute qpu"
		return res, nil
	}
	if j.JobData().Status == core.FAILED {
		zap.L().Error("The result status of QPU is FAILED")
		res.Message = fmt.Sprintf("Failed to execute qpu")
		return res, nil
	}

	if jd.Result != nil {
		res.Result = jd.Result.ToString()
		res.Message = j.JobData().Result.Message
	}
	res.Status = j.JobData().Status.String()
	zap.L().Info(fmt.Sprintf("Succeeded to transpile and execute for SSE, JobID:%s", jd.ID))
	zap.L().Debug(fmt.Sprintf("Response: %+v", res))
	return res, nil
}

func useDefaultTranspiler(jobDataJson string) bool {
	if jobDataJson == "" {
		zap.L().Debug("transpiler_info is blank")
		return true
	}
	jobDataMap := make(map[string]interface{})
	if err := json.Unmarshal([]byte(jobDataJson), &jobDataMap); err != nil {
		msg := fmt.Sprintf("Failed to unmarshal request. Reason:%s", err)
		zap.L().Error(msg)
		return true
	}
	if val, ok := jobDataMap["transpiler_info"]; ok {
		if val == nil {
			zap.L().Debug("transpiler_info is nil, use default transpiler")
			return true
		}
		transpiler_info := val.(map[string]interface{})
		if len(transpiler_info) == 0 {
			zap.L().Debug("transpiler_info is empty, use default transpiler")
			return true
		}
	}
	zap.L().Debug("do not use default transpiler")
	return false
}

func validateShots(shots int, deviceInfo *core.DeviceInfo) error {
	if deviceInfo == nil {
		return fmt.Errorf("DeviceInfo is nil")
	}
	if shots < 1 {
		return fmt.Errorf(fmt.Sprintf("The number of shots %d is less than 1", shots))
	}
	if shots > deviceInfo.MaxShots {
		return fmt.Errorf(fmt.Sprintf("The number of shots %d is over the limit %d", shots, deviceInfo.MaxShots))
	}
	return nil
}

func toJob(body []byte) (core.Job, error) {
	jd, err := toJobData(body)
	if err != nil {
		zap.L().Error(fmt.Sprintf("Failed to convert JSON to JobData. Reason:%s", err))
		return nil, err
	}
	jm := core.GetJobManager()
	jd.JobType = ssep.SSE_JOB
	jc, err := core.NewJobContext()
	if err != nil {
		zap.L().Error(fmt.Sprintf("Failed to create a job context. Reason:%s", err))
		return nil, err
	}
	return jm.NewJobFromJobData(jd, jc)
}

func toJobData(body []byte) (*core.JobData, error) {
	if body == nil || len(body) == 0 {
		return nil, fmt.Errorf("Invalid request. Reason: body is empty")
	}
	var userReq *UserReqData
	err := json.Unmarshal(body, &userReq)
	if err != nil {
		zap.L().Error(fmt.Sprintf("Failed to unmarshal user request. Reason:%s", err))
		return nil, err
	}

	// Convert UserReqData to JobData
	newJob := core.NewJobData()
	newJob.ID = userReq.ID
	newJob.QASM = userReq.Qasm
	newJob.Shots = int(userReq.Shots)
	newJob.Transpiler = userReq.Transpiler

	return newJob, nil
}

type SSEGRPCServer struct {
	server     *grpc.Server
	grpcServer sse.SSEServiceServer
}

func (m *SSEGRPCServer) Setup(container *dig.Container) error {
	sconf := sseconf.GetSSEConf()
	url := net.JoinHostPort(sconf.QmtRouterListenHost, fmt.Sprintf("%d", sconf.QmtRouterListenPort))

	// start gRPC server
	zap.L().Info(fmt.Sprintf("Starting up gRPC server. Listening on %s", url))
	listener, err := net.Listen("tcp", url)
	if err != nil {
		zap.L().Error(fmt.Sprintf("Failed to make gRPC server. Reason:%s", err))
		return err
	}
	m.server = grpc.NewServer()
	m.grpcServer = &GRPCRouter{container: container}
	sse.RegisterSSEServiceServer(m.server, m.grpcServer)
	go func() {
		err = m.server.Serve(listener)
	}()
	return nil
}

func (m *SSEGRPCServer) TearDown() {
	// m.server.GracefulStop() // this blocks shutdown process until reqtranspile completes
}
