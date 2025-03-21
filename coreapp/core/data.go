package core

import (
	"fmt"
	"time"

	"github.com/go-openapi/strfmt"
	jsoniter "github.com/json-iterator/go"
	"github.com/mohae/deepcopy"
	"github.com/tidwall/pretty"
	"go.uber.org/zap"
)

type Status int // Status of the job known to the cloud that is not as the same meaning in edge.

type PhysicalVirtualMapping map[uint32]uint32
type VirtualPhysicalMapping map[uint32]uint32
type Counts map[string]uint32

func (c Counts) String() string {
	// json string
	st, err := json.Marshal(c)
	if err != nil {
		zap.L().Error("Failed to marshal core.Counts")
		return ""
	}
	return string(st)
}

func (p PhysicalVirtualMapping) String() string {
	// json string
	st, err := json.Marshal(p)
	if err != nil {
		zap.L().Error("Failed to marshal core.PhysicalVirtualMapping")
		return ""
	}
	return string(st)
}

func (v VirtualPhysicalMapping) String() string {
	// json string
	st, err := json.Marshal(v)
	if err != nil {
		zap.L().Error("Failed to marshal core.VirtualPhysicalMapping")
		return ""
	}
	return string(st)
}

type DividedResult map[uint32]map[string]uint32 // key1: circuit index, key2: bit string, value: count

var json = jsoniter.ConfigCompatibleWithStandardLibrary

const (
	SUBMITTED Status = iota // In the queue in the cloud server.
	READY                   // Has never been processed on QPU. All the jobs in edge are in this status at first.
	RUNNING                 // Ing processed and has been processed on QPU in engine server.
	SUCCEEDED               // Finished successfully.
	FAILED                  // Finished with failure.
	CANCELLED               // Finished with cancellation.
)

func (s Status) String() string {
	switch s {
	case SUBMITTED:
		return "submitted"
	case READY:
		return "ready"
	case RUNNING:
		return "running"
	case SUCCEEDED:
		return "succeeded"
	case FAILED:
		return "failed"
	case CANCELLED:
		return "cancelled"
	default:
		return "unknown"
	}
}

func ToStatus(s string) (Status, error) {
	switch s {
	case "submitted":
		return SUBMITTED, nil
	case "ready":
		return READY, nil
	case "running":
		return RUNNING, nil
	case "succeeded":
		return SUCCEEDED, nil
	case "failed":
		return FAILED, nil
	case "cancelled":
		return CANCELLED, nil
	default:
		return 0, fmt.Errorf("unknown status: %s", s)
	}
}

type Result struct {
	Counts         Counts          `json:"counts"`
	DividedResult  DividedResult   `json:"divided_result"`
	TranspilerInfo *TranspilerInfo `json:"transpiler_info"`
	Estimation     *Estimation     `json:"estimation"`
	Message        string          `json:"message"`
	ExecutionTime  time.Duration   `json:"execution_time"`
}

type TranspilerInfo struct {
	Stats                  string                 `json:"stats"`
	PhysicalVirtualMapping PhysicalVirtualMapping `json:"physical_virtual_mapping"` // TODO: remove
	VirtualPhysicalMapping VirtualPhysicalMapping `json:"virtual_physical_mapping"`
}

type Property struct {
	QubitIndex             uint32 `json:"qubit_index"`
	MeasurementWindowIndex uint32 `json:"measurement_window_index"`
}

type Estimation struct {
	Exp_value float32 `json:"exp_value"`
	Stds      float32 `json:"stds"`
}

func cloneCounts(counts Counts) Counts {
	clone := make(Counts)
	for k, v := range counts {
		clone[k] = v
	}
	return clone
}

func cloneTranspilerInfo(info *TranspilerInfo) *TranspilerInfo {
	clone := &TranspilerInfo{}
	clone.PhysicalVirtualMapping = make(PhysicalVirtualMapping)
	clone.VirtualPhysicalMapping = make(VirtualPhysicalMapping)
	for k, v := range info.PhysicalVirtualMapping {
		clone.PhysicalVirtualMapping[k] = v
	}
	for k, v := range info.VirtualPhysicalMapping {
		clone.VirtualPhysicalMapping[k] = v
	}
	return clone
}

func cloneEstimation(estimation *Estimation) *Estimation {
	clone := &Estimation{}
	clone.Exp_value = estimation.Exp_value
	clone.Stds = estimation.Stds
	return clone
}

type JobData struct {
	ID             string
	Status         Status
	Shots          int
	Transpiler     *TranspilerConfig
	QASM           string
	TranspiledQASM string
	Result         *Result
	JobType        string
	Created        strfmt.DateTime
	Ended          strfmt.DateTime
	Info           string
	MitigationInfo string

	// VeryAdhoc
	UseJobInfoUpdate bool
}

func (jd *JobData) Clone() *JobData {
	c := deepcopy.Copy(jd).(*JobData)
	c.Created = *jd.Created.DeepCopy()
	c.Ended = *jd.Ended.DeepCopy()
	c.UseJobInfoUpdate = jd.UseJobInfoUpdate // TODO: fix this very adhoc
	return c
}

func (jd *JobData) NeedTranspiling() bool {
	return jd.Transpiler.TranspilerLib != nil
}

func NewResult() *Result {
	ti := &TranspilerInfo{}
	ti.PhysicalVirtualMapping = make(PhysicalVirtualMapping)
	ti.VirtualPhysicalMapping = make(VirtualPhysicalMapping)
	return &Result{
		Counts: make(Counts),
		// TODO: fix this
		// DividedResult:  make(DividedResult),
		//Estimation:     &Estimation{},
		TranspilerInfo: ti,
	}
}

func NewJobData() *JobData {
	return &JobData{
		Result:  NewResult(),
		Created: strfmt.DateTime(time.Now()),
	}
}

func NewJobDataWithoutCreated() *JobData {
	return &JobData{
		Result: NewResult(),
	}
}

func CloneJobData(i *JobData) *JobData {
	o := NewJobData()
	o.ID = i.ID
	o.Status = i.Status
	o.Shots = i.Shots
	o.Transpiler = i.Transpiler
	o.QASM = i.QASM
	o.TranspiledQASM = i.TranspiledQASM
	o.Result.Counts = cloneCounts(i.Result.Counts)
	o.Result.TranspilerInfo = cloneTranspilerInfo(i.Result.TranspilerInfo)
	o.JobType = i.JobType
	o.Created = i.Created
	o.Ended = i.Ended
	if i.JobType == "estimation" {
		o.Result.Estimation = cloneEstimation(i.Result.Estimation)
	}
	return o
}

func (r *Result) ToString() string {
	// Marshal to json with key sorted
	st, err := json.Marshal(r)
	if err != nil {
		zap.L().Error("Failed to marshal core.Result")
		return ""
	}
	st = pretty.Pretty(st)
	return string(st)
}

type TranspilerConfig struct {
	TranspilerLib     *string           `json:"transpiler_lib"` //(=nil) null means no transpiler
	TranspilerOptions TranspilerOptions `json:"transpiler_options"`
}

type TranspilerOptions struct {
	OptimizationLevel int `json:"optimization_level"`
}

func (c TranspilerConfig) NeedTranspiling() bool {
	return c.TranspilerLib != nil
}

func UnmarshalToTranspilerConfig(transpilerInfo string) TranspilerConfig {
	var c TranspilerConfig
	err := json.Unmarshal([]byte(transpilerInfo), &c)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to unmarshal transpiler config from :%s/reason:%s",
			transpilerInfo, err))
	}
	return c
}
