package core

import (
	"encoding/json"
	"fmt"
	"strconv"
	"time"

	"github.com/go-openapi/strfmt"
	jsoniter "github.com/json-iterator/go"
	"github.com/mohae/deepcopy"
	"github.com/tidwall/pretty"
	"go.uber.org/zap"
)

type Status int               // Status of the job known to the cloud that is not as the same meaning in edge.
type StatsRaw json.RawMessage // TODO: make StatsMap
type PhysicalVirtualMapping map[uint32]uint32
type VirtualPhysicalMappingRaw json.RawMessage
type VirtualPhysicalMappingMap map[uint32]uint32
type Counts map[string]uint32

var jsonIter = jsoniter.ConfigCompatibleWithStandardLibrary

func NewStatsRawFromString(s string) (StatsRaw, error) {
	raw, err := json.Marshal(s)
	if err != nil {
		return nil, err
	}
	return raw, nil
}

func (c Counts) String() string {
	st, err := jsonIter.Marshal(c)
	if err != nil {
		zap.L().Error("Failed to marshal core.Counts")
		return ""
	}
	return string(st)
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

func (p PhysicalVirtualMapping) String() string {
	st, err := jsonIter.Marshal(p)
	if err != nil {
		zap.L().Error("Failed to marshal core.PhysicalVirtualMapping")
		return ""
	}
	return string(st)
}

func (v VirtualPhysicalMappingRaw) String() string {
	st, err := jsonIter.Marshal(v)
	if err != nil {
		zap.L().Error("Failed to marshal core.VirtualPhysicalMapping")
		return ""
	}
	return string(st)
}

func (v VirtualPhysicalMappingRaw) ToMap() (VirtualPhysicalMappingMap, error) {
	// Since JSON object keys are always strings, unmarshaling directly into a map[uint32]uint32
	// will result in an error. Therefore, we first unmarshal into a map[string]uint32,
	// and then convert it to a map[uint32]uint32.
	var temp map[string]uint32
	if err := json.Unmarshal(v, &temp); err != nil {
		zap.L().Error(fmt.Sprintf("failed to unmarshal VirtualPhysicalMappingMapRaw:%v/reason:%s",
			v, err))
	}

	result := make(map[uint32]uint32)
	for k, v := range temp {
		key, err := strconv.ParseUint(k, 10, 32)
		if err != nil {
			zap.L().Error(fmt.Sprintf("failed to convert key:%s/reason:%s", k, err))
			return nil, err
		}
		result[uint32(key)] = v
	}
	return result, nil
}

func (v VirtualPhysicalMappingMap) ToRaw() (VirtualPhysicalMappingRaw, error) {
	b, err := json.Marshal(v)
	if err != nil {
		return nil, err
	}
	return b, nil
}

type DividedResult map[uint32]map[string]uint32 // key1: circuit index, key2: bit string, value: count

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

type Result struct {
	Counts         Counts          `json:"counts"`
	DividedResult  DividedResult   `json:"divided_result"`
	TranspilerInfo *TranspilerInfo `json:"transpiler_info"`
	Estimation     *Estimation     `json:"estimation"`
	Message        string          `json:"message"`
	ExecutionTime  time.Duration   `json:"execution_time"`
}

type TranspilerInfo struct {
	StatsRaw                  StatsRaw                  `json:"stats"`
	PhysicalVirtualMapping    PhysicalVirtualMapping    `json:"physical_virtual_mapping"` // TODO: remove
	VirtualPhysicalMappingRaw VirtualPhysicalMappingRaw `json:"virtual_physical_mapping"`
	VirtualPhysicalMappingMap VirtualPhysicalMappingMap `json:"-"` // TODO unify with VirtualPhysicalMappingRaw
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

	for k, v := range info.PhysicalVirtualMapping {
		clone.PhysicalVirtualMapping[k] = v
	}
	clone.VirtualPhysicalMappingRaw = VirtualPhysicalMappingRaw(append(json.RawMessage(nil), info.VirtualPhysicalMappingRaw...))
	for k, v := range info.VirtualPhysicalMappingMap {
		clone.VirtualPhysicalMappingMap[k] = v
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
	UseJobInfoUpdate          bool
	NeedsUpdateTranspilerInfo bool
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
	st, err := jsonIter.Marshal(r)
	if err != nil {
		zap.L().Error("Failed to marshal core.Result")
		return ""
	}
	st = pretty.Pretty(st)
	return string(st)
}

// TODO resolve the confusion between TranspilerConfig and TranspilerInfo
type TranspilerConfig struct {
	TranspilerLib     *string         `json:"transpiler_lib"` //(=nil) null means no transpiler
	TranspilerOptions json.RawMessage `json:"transpiler_options"`
	UseDefault        bool            `json:"-"`
}

func (c TranspilerConfig) NeedTranspiling() bool {
	return c.TranspilerLib != nil
}

func UnmarshalToTranspilerConfig(transpilerInfo string) TranspilerConfig {
	var c TranspilerConfig
	err := jsonIter.Unmarshal([]byte(transpilerInfo), &c)
	if err != nil {
		zap.L().Error(fmt.Sprintf("failed to unmarshal transpiler config from :%s/reason:%s",
			transpilerInfo, err))
	}
	return c
}
