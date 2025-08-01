package multiprog

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/go-openapi/strfmt"
	"github.com/oqtopus-team/oqtopus-engine/coreapp/core"
	mpgmconf "github.com/oqtopus-team/oqtopus-engine/coreapp/multiprog/manual/conf"
	pb "github.com/oqtopus-team/oqtopus-engine/coreapp/multiprog/manual/multiprog_interface/v1"
	rd "github.com/oqtopus-team/oqtopus-engine/coreapp/multiprog/manual/resultdivider"
	"go.uber.org/zap"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

var (
	ErrorJobIDConflict      = errors.New("jobID is already used")
	ErrorCircuitCombineFail = errors.New("circuit combine failed")
	ErrorBadRequest         = errors.New("bad request")
)

const (
	MULTIPROG_MANUAL_JOB      = "multi_manual"
	MPG_MANUAL_ENV_FILE_NAME  = "multiprog/manual/.mpgmenv"
	CIRCUIT_COMBINER_PORT_KEY = "COMBINER_PORT"
	CIRCUIT_COMBINER_HOST_KEY = "COMBINER_HOST"
)

type ManualJob struct {
	jobData            *core.JobData
	jobContext         *core.JobContext
	combinedQubitsList []int32
	combinedQASM       string
	originalQASMs      string

	postProcessed bool
}

func (j *ManualJob) New(jd *core.JobData, jc *core.JobContext) core.Job {
	return &ManualJob{
		jobData:            jd,
		jobContext:         jc,
		combinedQubitsList: make([]int32, 0),
		combinedQASM:       "",
		originalQASMs:      "",
		postProcessed:      false,
	}
}

func (j *ManualJob) JobData() *core.JobData {
	return j.jobData
}

func (j *ManualJob) JobType() string {
	return MULTIPROG_MANUAL_JOB
}

func (j *ManualJob) JobContext() *core.JobContext {
	return j.jobContext
}

func (j *ManualJob) UpdateStatus(st core.Status) {
	j.jobData.Status = st
}

func (j *ManualJob) IsFinished() bool {
	return j.postProcessed || j.JobData().Status == core.FAILED
}

func (j *ManualJob) PreProcess() {
	jd := j.JobData()
	p := &core.JobParam{
		JobID:      jd.ID,
		QASM:       jd.QASM,
		Shots:      jd.Shots,
		Transpiler: jd.Transpiler,
		JobType:    jd.JobType,
	}
	if err := validateJobParam(p); err != nil {
		zap.L().Info(fmt.Sprintf("failed to validate job data. Reason:%s", err.Error()))
		core.SetFailureWithError(j, err)
		return
	}

	j.originalQASMs = jd.QASM
	if err := j.preProcessImpl(); err != nil {
		zap.L().Error(fmt.Sprintf("failed to pre-process a job(%s). Reason:%s",
			jd.ID, err.Error()))
		j.rollbackQASM()
		core.SetFailureWithError(j, err)
		jd.UseJobInfoUpdate = true
		return
	}
}

func (j *ManualJob) preProcessImpl() (err error) {
	err = nil
	jd := j.JobData()
	container := core.GetSystemComponents().Container
	err = container.Invoke(
		func(d core.DBManager) error {
			return d.Insert(j)
		})
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to insert a job(%s). Reason:%s", jd.ID, err.Error()))
		return
	}
	// send Job to circuit_combiner
	mpgmconf := mpgmconf.GetMPGMConf()

	combined_qasm, combined_qubits_list, err := sendJobdata(j, mpgmconf)
	if err != nil {
		jd.Result.Message = err.Error()
		zap.L().Error(fmt.Sprintf("failed to combine a job(%s). Reason:%s", jd.ID, err.Error()))
		return
	}
	j.combinedQubitsList = combined_qubits_list
	jd.QASM = combined_qasm // this field is processd on QPU
	j.combinedQASM = combined_qasm

	err = container.Invoke(
		func(t core.Transpiler) error {
			return t.Transpile(j)
		})
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to transpile a job(%s). Reason:%s", jd.ID, err.Error()))
		return
	}

	return
}

func (j *ManualJob) Process() {
	c := core.GetSystemComponents().Container
	err := c.Invoke(
		func(q core.QPUManager) error {
			return q.Send(j)
		})
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to send a job(%s) to QPU. Reason:%s", j.JobData().ID, err.Error()))
		j.jobData.Status = core.FAILED
		j.rollbackQASM()
		return
	}
	zap.L().Debug(fmt.Sprintf("PostProcess goroutine for job(%s) is started", j.JobData().ID))
}

func (j *ManualJob) PostProcess() {
	jd := j.JobData()
	j.postProcessed = true

	if err := j.setQASMJson(); err != nil {
		zap.L().Error(fmt.Sprintf("failed to set qasm json in a job(%s). Reason:%s", jd.ID, err.Error()))
		core.SetFailureWithError(j, fmt.Errorf("Post-process failed"))
		j.rollbackQASM()
		return
	}

	// Split the result from the called job
	if err := rd.DivideResult(jd, j.combinedQubitsList); err != nil {
		zap.L().Error(fmt.Sprintf("failed to divide a job(%s). Reason:%s", jd.ID, err.Error()))
		core.SetFailureWithError(j, fmt.Errorf("Post-process failed"))
		return
	}
	// Update DB
	jd.Status = core.SUCCEEDED
	jd.Ended = strfmt.DateTime(time.Now())
}

func (j *ManualJob) setQASMJson() (err error) {
	// set combined circuit to jobData.QASM
	err = nil

	// Unmarshal the jobData.QASM string into a map
	var originalQasmArray []string
	err = json.Unmarshal([]byte(j.originalQASMs), &originalQasmArray)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to unmarshal original QASM string in a job(%s). Reason:%s", j.JobData().ID, err.Error()))
		j.rollbackQASM()
		return
	}

	allQasmMap := make(map[string]string)
	allQasmMap["combined_qasm"] = j.combinedQASM
	allQasmMap["original_qasms"] = j.originalQASMs

	// Marshal the map back into a JSON string
	qasmJSON, err := json.Marshal(allQasmMap)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to marshal QASM map in a job(%s). Reason:%s", j.JobData().ID, err.Error()))
		j.rollbackQASM()
		return
	}
	j.jobData.QASM = string(qasmJSON)
	return
}

func (j *ManualJob) rollbackQASM() {
	j.jobData.QASM = j.originalQASMs
}

func (j *ManualJob) Clone() core.Job {
	copy_combined_qubits_list := make([]int32, len(j.combinedQubitsList))
	copy(copy_combined_qubits_list, j.combinedQubitsList)
	cloned := &ManualJob{
		jobData:            j.jobData.Clone(),
		jobContext:         j.jobContext,
		combinedQubitsList: copy_combined_qubits_list,
		combinedQASM:       j.combinedQASM,
		originalQASMs:      j.originalQASMs,
	}
	return cloned
}

func validateJobParam(p *core.JobParam) (err error) {
	err = nil
	if p.Shots <= 0 {
		msg := fmt.Sprintf("shots(%d) must be greater than 0", p.Shots)
		zap.L().Info(msg + fmt.Sprintf("/jobID:%s", p.JobID))
		return fmt.Errorf(msg)
	}
	maxShots := core.GetSystemComponents().GetDeviceInfo().MaxShots
	if p.Shots > maxShots {
		msg := fmt.Sprintf("shots(%d) is over the limit(%d)",
			p.Shots, maxShots)
		zap.L().Info(msg + fmt.Sprintf("/jobID:%s", p.JobID))
		return fmt.Errorf(msg)
	}

	container := core.GetSystemComponents().Container
	err = container.Invoke(
		func(t core.Transpiler) error {
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

	return err
}

func sendJobdata(inputJob core.Job, mpgmconf *mpgmconf.MPGMConf) (combinedQASM string, combinedQubitsList []int32, err error) {
	// Send Job information to python-hosted-gRPC server
	combinedQASM = ""
	combinedQubitsList = []int32{}
	err = nil

	// extract port number from environment variable
	port := mpgmconf.CircuitCombinerPort
	if port == "" {
		port = "5002"
		zap.L().Warn(fmt.Sprintf("%s is not set. Use default port: %s", CIRCUIT_COMBINER_PORT_KEY, port))
	}
	// extract host from environment variable
	host := mpgmconf.CircuitCombinerHost
	zap.L().Debug(fmt.Sprintf("port: %s, host: %s", port, host))

	if host == "" {
		host = "localhost"
		zap.L().Warn(fmt.Sprintf("%s is not set. Use default host: %s", CIRCUIT_COMBINER_HOST_KEY, host))
	}

	// create new insecure credential
	opts := grpc.WithTransportCredentials(insecure.NewCredentials())
	// connect server
	conn, err := grpc.NewClient(fmt.Sprintf("%s:%s", host, port), opts)
	if err != nil {
		zap.L().Error(fmt.Sprintf("did not connect: %v", err))
	}
	defer conn.Close()

	device_info := core.GetSystemComponents().GetDeviceInfo()
	// create gRPC client
	client := pb.NewCircuitCombinerServiceClient(conn)

	outputJobData := core.CloneJobData(inputJob.JobData())
	qasm_str_without_newline := strings.ReplaceAll(outputJobData.QASM, "\n", "")
	qasm_str_processed := strings.ReplaceAll(qasm_str_without_newline, "\"", "\\\"")
	req := &pb.CombineRequest{
		QasmArray: qasm_str_processed,
		MaxQubits: int32(device_info.MaxQubits),
	}
	// send request to gRPC server
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	res, err := client.Combine(ctx, req)
	if err != nil {
		zap.L().Error(fmt.Sprintf("could not request: %v", err))
		return
	}

	// check response
	if res.CombinedStatus != pb.Status_STATUS_SUCCESS {
		zap.L().Error(fmt.Sprintf("failed to combine circuits: the status is %s", res.CombinedStatus.String()))
		err = ErrorCircuitCombineFail
		return
	}
	// set combined_qasm and combined_info
	combinedQASM = res.CombinedQasm
	combinedQubitsList = res.CombinedQubitsList

	// print response
	zap.L().Debug(fmt.Sprintf("Status: %s \nResult: %s\n",
		res.CombinedStatus.String(), res.CombinedQasm))

	return
}
